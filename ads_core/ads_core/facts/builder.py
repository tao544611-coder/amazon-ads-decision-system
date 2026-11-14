# -*- coding: utf-8 -*-
"""事实层构建：canonical 汇总 → metric_daily（设计 §4；事实层禁读源报表 D11）。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..methodology import require_released_version
from ..methodology.params import (
    get_param_group,
    maturity_days,
    mixed_maturity_days,
)
from ..storage.audit import write_audit
from ..storage.models import (
    CampaignMap,
    ImportBatch,
    MetricDaily,
    QualityFinding,
    RawDataset,
    now_iso,
)
from .metrics import compute_metrics
from .maturity import maturity_for

GRAIN_COLUMNS = {
    "campaign": ["campaign_id"],
    "adgroup": ["campaign_id", "adgroup_id"],
    "target": ["campaign_id", "adgroup_id", "target_id"],
    "searchterm": ["campaign_id", "adgroup_id", "target_id", "search_term"],
    "placement": ["campaign_id", "placement"],
    "product": ["product_id"],
}

GRAIN_SOURCE = {
    "campaign": "campaign",
    "adgroup": "adgroup",
    "target": "target",
    "searchterm": "searchterm",
    "placement": "placement",
}


@dataclass
class FactsResult:
    rows_written: int = 0
    rows_skipped_existing: int = 0
    rows_skipped_immature: int = 0
    by_grain: dict[str, int] = field(default_factory=dict)
    maturity_stat: dict[str, int] = field(default_factory=dict)
    qf10: dict | None = None


def _keys_hash(grain: str, keys: dict) -> str:
    payload = json.dumps([grain, {k: keys.get(k) or "" for k in GRAIN_COLUMNS[grain]}], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _active_raws(session: Session, batch_id: str | None) -> dict[str, RawDataset]:
    """参与事实计算的 raw_dataset（排除 superseded/rejected；可选限定批次）。"""
    stmt = select(RawDataset).where(RawDataset.status == "normalized")
    if batch_id:
        stmt = stmt.where(RawDataset.batch_id == batch_id)
    return {r.id: r for r in session.scalars(stmt).all()}


def build_facts(
    session: Session,
    batch_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    grains: list[str] | None = None,
    mature_only: bool = False,
    actor: str = "cli",
) -> FactsResult:
    version = require_released_version(session)
    sp_days = maturity_days(session, "sp", version.id)
    sb_sd_days = maturity_days(session, "sb", version.id)
    sb_days = mixed_maturity_days(session, version.id)  # 混算从严阈值（HR-02）

    requested = grains or list(GRAIN_SOURCE.keys()) + ["product"]
    result = FactsResult()

    campaign_product: dict[tuple[str, str], str | None] = {}
    for cm in session.scalars(select(CampaignMap)).all():
        # 校准口径：canonical.campaign_id 可能装报表 Id 或活动名（后台报表多无 Id 列），
        # 映射按 campaign_id 与 campaign_name 双键匹配，先注册者优先
        campaign_product.setdefault((cm.campaign_id, cm.ad_product_type or ""), cm.product_id)
        if cm.campaign_name:
            campaign_product.setdefault((cm.campaign_name, cm.ad_product_type or ""), cm.product_id)

    raw_index = _active_raws(session, batch_id)

    for grain in requested:
        if grain == "product":
            rows = _collect_product_rows(session, raw_index, campaign_product, date_from, date_to)
        else:
            rows = _collect_rows(session, grain, raw_index, date_from, date_to)

        for (_key_tuple, row_date, ad_type), (keys, agg) in rows.items():
            metrics = compute_metrics(
                agg["impressions"], agg["clicks"], agg["spend"], agg["orders"], agg["sales"]
            )
            threshold = sb_sd_days if ad_type in {"sb", "sd"} else sp_days
            maturity = maturity_for(row_date, ad_type, agg["data_as_of"], sp_days, threshold)
            if mature_only and maturity != "mature":
                result.rows_skipped_immature += 1
                result.maturity_stat[maturity] = result.maturity_stat.get(maturity, 0) + 1
                continue
            existing = session.scalars(
                select(MetricDaily).where(
                    MetricDaily.grain == grain,
                    MetricDaily.grain_keys_hash == _keys_hash(grain, keys),
                    MetricDaily.date == row_date,
                    MetricDaily.ad_product_type == ad_type,
                    MetricDaily.methodology_version_id == version.id,
                )
            ).first()
            if existing is not None:
                result.rows_skipped_existing += 1
                continue

            product_id = keys.get("product_id") if grain == "product" else None
            if grain == "campaign":
                cid, cname = keys.get("campaign_id"), keys.get("campaign_name")
                product_id = (
                    campaign_product.get((cid, ad_type))
                    or campaign_product.get((cid, ""))
                    or campaign_product.get((cname, ad_type))
                    or campaign_product.get((cname, ""))
                )

            session.add(MetricDaily(
                grain=grain,
                grain_keys_json=json.dumps(keys, ensure_ascii=False),
                grain_keys_hash=_keys_hash(grain, keys),
                product_id=product_id,
                date=row_date,
                marketplace=agg["marketplace"],
                currency=agg["currency"],
                ad_product_type=ad_type,
                impressions=agg["impressions"],
                clicks=agg["clicks"],
                spend=agg["spend"],
                orders=agg["orders"],
                sales=agg["sales"],
                units=agg["units"],
                ctr=metrics.ctr,
                cpc=metrics.cpc,
                cvr=metrics.cvr,
                acos=metrics.acos,
                roas=metrics.roas,
                cpa=metrics.cpa,
                computable_json=json.dumps(metrics.computable, ensure_ascii=False),
                maturity=maturity,
                batch_refs_json=json.dumps(agg["batch_refs"], ensure_ascii=False),
                methodology_version_id=version.id,
                formula_version=version.version_no,
                computed_at=now_iso(),
            ))
            result.rows_written += 1
            result.by_grain[grain] = result.by_grain.get(grain, 0) + 1
            result.maturity_stat[maturity] = result.maturity_stat.get(maturity, 0) + 1

    # QF-10：成熟度概览（info，不阻断）
    total = sum(result.maturity_stat.values())
    if total:
        immature = result.maturity_stat.get("immature_observation", 0)
        result.qf10 = {
            "total_rows": total,
            "immature_rows": immature,
            "immature_ratio": round(immature / total, 4),
            "sp_min_days": sp_days,
            "mixed_min_days": sb_days,
        }
        if batch_id:
            session.add(QualityFinding(
                batch_id=batch_id,
                check_code="QF-10",
                severity="info",
                message=f"成熟度概览：{immature}/{total} 行未成熟观察（占比 {immature / total:.1%}）",
                details_json=json.dumps(result.qf10, ensure_ascii=False),
                created_at=now_iso(),
            ))

    write_audit(session, actor, "facts.build", "metric_daily", batch_id or "*",
                {"rows_written": result.rows_written, "by_grain": result.by_grain})
    return result


def _new_agg(raw: RawDataset) -> dict:
    """度量初始为 None：缺源列保持 None（→ missing_source），不虚构 0（PRD 4.5 红线）。"""
    return {
        "impressions": None, "clicks": None, "spend": None,
        "orders": None, "sales": None, "units": None,
        "marketplace": raw.marketplace or "UK",
        "currency": raw.currency or "GBP",
        "data_as_of": raw.data_as_of,
        "batch_refs": [],
    }


def _absorb(agg: dict, row, raw: RawDataset) -> None:
    for m in ("impressions", "clicks", "orders", "units", "spend", "sales"):
        v = getattr(row, m)
        if v is None:
            continue
        agg[m] = (agg[m] or 0) + v
    if raw.data_as_of > agg["data_as_of"]:
        agg["data_as_of"] = raw.data_as_of
    ref = {"batch_id": row.batch_id, "raw_dataset_id": row.raw_dataset_id}
    if ref not in agg["batch_refs"]:
        agg["batch_refs"].append(ref)


def _collect_rows(session: Session, grain: str, raw_index: dict, date_from, date_to) -> dict:
    from ..storage.models import CANONICAL_TABLES

    model = CANONICAL_TABLES[GRAIN_SOURCE[grain]]
    stmt = select(model)
    rows = session.scalars(stmt).all()
    grouped: dict[tuple, dict] = {}
    for row in rows:
        raw = raw_index.get(row.raw_dataset_id)
        if raw is None:
            continue
        if date_from and row.date < date_from:
            continue
        if date_to and row.date > date_to:
            continue
        keys = {
            "campaign_id": row.campaign_id,
            "campaign_name": row.campaign_name,  # 双键匹配用（不参与 grain hash）
            "adgroup_id": getattr(row, "adgroup_id", None),
            "target_id": getattr(row, "target_id", None) or getattr(row, "target_text", None),
            "search_term": getattr(row, "search_term", None),
            "placement": getattr(row, "placement", None),
        }
        allowed = set(GRAIN_COLUMNS[grain]) | ({"campaign_name"} if grain == "campaign" else set())
        keys = {k: v for k, v in keys.items() if k in allowed}
        key_tuple = tuple(keys.get(c) or "" for c in GRAIN_COLUMNS[grain])
        gkey = (key_tuple, row.date, row.ad_product_type)
        entry = grouped.setdefault(gkey, {"keys": keys, "agg": _new_agg(raw)})
        _absorb(entry["agg"], row, raw)
    return {k: (v["keys"], v["agg"]) for k, v in grouped.items()}


def _collect_product_rows(session: Session, raw_index: dict, campaign_product: dict, date_from, date_to) -> dict:
    from ..storage.models import CanCampaignDaily

    rows = session.scalars(select(CanCampaignDaily)).all()
    grouped: dict[tuple, dict] = {}
    for row in rows:
        raw = raw_index.get(row.raw_dataset_id)
        if raw is None:
            continue
        pid = (
            campaign_product.get((row.campaign_id, row.ad_product_type))
            or campaign_product.get((row.campaign_id, ""))
            or campaign_product.get((row.campaign_name, row.ad_product_type))
            or campaign_product.get((row.campaign_name, ""))
        )
        if pid is None:
            continue
        if date_from and row.date < date_from:
            continue
        if date_to and row.date > date_to:
            continue
        keys = {"product_id": pid}
        key_tuple = (pid,)
        gkey = (key_tuple, row.date, row.ad_product_type)
        entry = grouped.setdefault(gkey, {"keys": keys, "agg": _new_agg(raw)})
        _absorb(entry["agg"], row, raw)
    return {k: (v["keys"], v["agg"]) for k, v in grouped.items()}
