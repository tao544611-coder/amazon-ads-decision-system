# -*- coding: utf-8 -*-
"""canonical 构建（设计 §3：仅 complete/partial 批次进入；事实层禁读源报表 D11）。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..adapters import ADAPTERS, AdapterError, CanonicalRow
from ..ingest.reader import read_table
from ..quality.batch_state import BLOCKED_FROM_CANONICAL
from ..quality.checks import CROSS_TOLERANCE, Finding, check_batch_caliber
from ..storage.audit import write_audit
from ..storage.models import (
    CANONICAL_TABLES,
    ImportBatch,
    RawDataset,
    now_iso,
)


@dataclass
class CanonicalResult:
    batch_id: str
    rows_written: dict[str, int] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    skipped_raw: list[str] = field(default_factory=list)


def _to_model(report_type: str, row: CanonicalRow, batch_id: str, raw_id: str):
    model = CANONICAL_TABLES[report_type]
    base = {
        "batch_id": batch_id,
        "raw_dataset_id": raw_id,
        "date": row.date,
        "marketplace": row.marketplace,
        "currency": row.currency,
        "ad_product_type": row.ad_product_type,
        "attribution_window_days": row.attribution_window_days,
        "campaign_id": row.campaign_id,
        "campaign_name": row.campaign_name,
        "campaign_status": row.campaign_status,
        "portfolio_name": row.portfolio_name,
        "impressions": row.impressions,
        "clicks": row.clicks,
        "spend": row.spend,
        "orders": row.orders,
        "sales": row.sales,
        "units": row.units,
        "attrs_json": json.dumps(row.attrs, ensure_ascii=False),
        "source_lineage_json": json.dumps(row.lineage, ensure_ascii=False),
        "created_at": now_iso(),
    }
    extras = {
        "adgroup_id": row.adgroup_id,
        "adgroup_name": row.adgroup_name,
        "target_id": row.target_id,
        "target_text": row.target_text,
        "match_type": row.match_type,
        "search_term": row.search_term,
        "placement": row.placement,
        "advertised_asin": row.advertised_asin,
        "advertised_sku": row.advertised_sku,
        "purchased_asin": row.purchased_asin,
    }
    cols = {c.name for c in model.__table__.columns}
    payload = {k: v for k, v in {**base, **extras}.items() if k in cols}
    return model(**payload)


def _cross_batch_check(
    session: Session,
    report_type: str,
    rows: list[CanonicalRow],
    batch: ImportBatch,
    raw: RawDataset,
) -> list[Finding]:
    """QF-07（同源重叠不一致 warn）/ QF-09（跨源差异超容差 error）。"""
    findings: list[Finding] = []
    if not rows:
        return findings
    model = CANONICAL_TABLES[report_type]
    keys = {(r.date, r.campaign_id) for r in rows}
    existing = session.scalars(
        select(model)
        .where(model.batch_id != batch.id, model.date.in_(k[0] for k in keys))
    ).all()
    index: dict[tuple[str, str], list] = {}
    for e in existing:
        index.setdefault((e.date, e.campaign_id), []).append(e)
    if not existing:
        return findings

    q07_conflicts, q09_conflicts = [], []
    for r in rows:
        for e in index.get((r.date, r.campaign_id), []):
            pairs = [("spend", r.spend, e.spend), ("clicks", r.clicks, e.clicks), ("impressions", r.impressions, e.impressions)]
            for name, new_v, old_v in pairs:
                if new_v is None or old_v is None:
                    continue
                base = max(abs(old_v), 1e-9)
                if abs(new_v - old_v) / base > CROSS_TOLERANCE:
                    entry = {
                        "date": r.date,
                        "campaign_id": r.campaign_id,
                        "field": name,
                        "this_value": new_v,
                        "other_value": old_v,
                        "other_batch": e.batch_id,
                    }
                    other_raw = session.get(RawDataset, e.raw_dataset_id)
                    other_system = other_raw.source_system if other_raw else "unknown"
                    entry["other_source_system"] = other_system
                    if other_system != batch.source_system:
                        q09_conflicts.append(entry)
                    else:
                        q07_conflicts.append(entry)
    if q07_conflicts:
        findings.append(
            Finding("QF-07", "warn", f"与既有批次同键同日数值不一致 {len(q07_conflicts)} 处（同源，待确认）", raw.id, {"conflicts": q07_conflicts[:20]})
        )
    if q09_conflicts:
        findings.append(
            Finding("QF-09", "error", f"跨源同键同日差异超容差({CROSS_TOLERANCE:.0%}) {len(q09_conflicts)} 处", raw.id, {"conflicts": q09_conflicts[:20]})
        )
    return findings


def build_canonical(session: Session, batch_id: str, actor: str = "cli") -> CanonicalResult:
    batch = session.get(ImportBatch, batch_id)
    if batch is None:
        raise ValueError(f"批次不存在：{batch_id}")
    if batch.status in BLOCKED_FROM_CANONICAL:
        raise ValueError(f"批次状态 {batch.status} 不允许进入 canonical（设计 §3.1）")

    result = CanonicalResult(batch_id=batch_id)
    raws = session.scalars(
        select(RawDataset).where(RawDataset.batch_id == batch_id)
    ).all()

    for raw in raws:
        if raw.status == "normalized":
            result.skipped_raw.append(raw.id)
            continue
        if raw.status in {"superseded", "rejected"}:
            result.skipped_raw.append(raw.id)
            continue
        if not raw.report_type or raw.report_type == "unknown":
            result.findings.append(Finding("QF-01", "error", "报表类型 unknown，跳过 canonical", raw.id, {}))
            continue

        adapter_cls = ADAPTERS.get(batch.source_system)
        if adapter_cls is None:
            raise AdapterError(f"无可用源适配器：source_system={batch.source_system}（D11：新源需增补适配器）")
        adapter = adapter_cls()
        table = read_table(raw.stored_path)
        rows = adapter.transform(raw, table)

        cross = _cross_batch_check(session, raw.report_type, rows, batch, raw)
        result.findings.extend(cross)
        if any(f.severity == "error" for f in cross):
            continue  # QF-09 error：该文件不进 canonical

        for row in rows:
            session.add(_to_model(raw.report_type, row, batch_id, raw.id))
        result.rows_written[raw.report_type] = result.rows_written.get(raw.report_type, 0) + len(rows)
        raw.status = "normalized"

    # 批级口径复检（QF-06 兜底）
    result.findings.extend(check_batch_caliber(session, batch_id))

    write_audit(
        session, actor, "canonical.build", "import_batch", batch_id,
        {"rows_written": result.rows_written, "findings": len(result.findings)},
    )
    return result
