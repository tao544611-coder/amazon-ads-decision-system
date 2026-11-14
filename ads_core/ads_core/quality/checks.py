# -*- coding: utf-8 -*-
"""质检项 QF-01~10（设计 §3.2）。

文件级：QF-01~06、QF-08（导入时执行）；
跨批/跨源：QF-07、QF-09（canonical 构建前 run_cross_checks）；
事实层：QF-10（facts 构建时产出 info）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ingest.detector import DetectionResult, load_fingerprints, normalize_header
from ..ingest.reader import TableData, parse_date, parse_number
from ..storage.models import RawDataset

# 跨批比对容差（QF-07/QF-09）
CROSS_TOLERANCE = 0.01


@dataclass
class Finding:
    check_code: str
    severity: str  # error|warn|info
    message: str
    raw_dataset_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def _metric_alias_map(fingerprints: dict[str, Any]) -> dict[str, list[str]]:
    return {k: [normalize_header(a) for a in v] for k, v in fingerprints["metric_aliases"].items()}


def _pick_col(norm_row_keys: set[str], aliases: list[str]) -> str | None:
    for a in aliases:
        if a in norm_row_keys:
            return a
    return None


def _first_col(norm_row: dict[str, str], candidates: list[str]) -> str:
    """校准（O-1）：无 Id 列时以 Name 列兜底取键。"""
    for c in candidates:
        if norm_row.get(c):
            return norm_row[c]
    return ""


def _business_key_for(report_type: str, row: dict[str, str]) -> str:
    campaign = _first_col(row, ["campaign id", "campaign name"])
    adgroup = _first_col(row, ["ad group id", "ad group name"])
    target = _first_col(row, ["keyword or product targeting", "targeting"])
    key_cols = {
        "campaign": [row.get("date", ""), campaign],
        "adgroup": [row.get("date", ""), campaign, adgroup],
        "target": [row.get("date", ""), campaign, adgroup, target],
        # 校准（真实样本 20261112）：搜索词键需含 target 维度、商品键需含 SKU 维度，
        # 否则真实数据中同词跨 target / 同 ASIN 多 SKU 会被误判重复（QF-02 误报）
        "searchterm": [row.get("date", ""), campaign, adgroup, target,
                       row.get("customer search term", "")],
        "placement": [row.get("date", ""), campaign, row.get("placement", "")],
        "advertised_product": [row.get("date", ""), campaign, adgroup,
                               _first_col(row, ["advertised asin", "asin"]),
                               row.get("advertised sku", "")],
        "purchased_product": [row.get("date", ""), campaign, adgroup,
                              row.get("advertised asin", ""),
                              row.get("advertised sku", ""),
                              _first_col(row, ["purchased asin", "other asin"])],
    }.get(report_type, [row.get("date", ""), campaign])
    return "|".join(key_cols)


def check_file_level(
    raw: RawDataset,
    table: TableData,
    detection: DetectionResult,
    fingerprints: dict[str, Any] | None = None,
) -> list[Finding]:
    """导入时文件级检查：QF-01/02/03/04/05。"""
    findings: list[Finding] = []
    fp = fingerprints or load_fingerprints()
    aliases = _metric_alias_map(fp)
    report_type = raw.report_type or "unknown"

    # QF-01：必要字段缺失——识别失败或表头缺列
    if detection.is_unknown or report_type == "unknown":
        findings.append(
            Finding(
                "QF-01",
                "error",
                "报表类型识别失败（unknown），缺必要列或置信度不足",
                raw.id,
                {"ambiguities": detection.ambiguities, "missing": detection.missing_required},
            )
        )
        return findings
    if detection.missing_required:
        findings.append(
            Finding("QF-01", "error", "必要列缺失", raw.id, {"missing": detection.missing_required})
        )
        return findings
    if len(table) == 0:
        findings.append(Finding("QF-01", "error", "数据体为空（0 行）", raw.id, {}))
        return findings

    norm_rows = [{normalize_header(k): v for k, v in row.items()} for row in table.rows]

    # QF-02：批内重复业务键
    seen: dict[str, dict[str, str]] = {}
    dup_same, dup_conflict = [], []
    metric_norm = [a for al in aliases.values() for a in al]
    for row in norm_rows:
        key = _business_key_for(report_type, row)
        if key in seen:
            prev = seen[key]
            measures_changed = any(
                (row.get(c) or "") != (prev.get(c) or "")
                for c in metric_norm
                if c in row or c in prev
            )
            (dup_conflict if measures_changed else dup_same).append(key)
        else:
            seen[key] = row
    if dup_same:
        findings.append(
            Finding("QF-02", "warn", f"批内重复业务键 {len(dup_same)} 组（度量值一致）", raw.id, {"keys": dup_same[:20]})
        )
    if dup_conflict:
        findings.append(
            Finding("QF-02", "error", f"批内重复业务键 {len(dup_conflict)} 组且度量值不一致", raw.id, {"keys": dup_conflict[:20]})
        )

    # QF-03：格式异常（数值非数值 / 日期解析失败 / 负值）
    bad_num: list[dict[str, str]] = []
    bad_date = 0
    negative_hits: list[dict[str, str]] = []
    date_col = _pick_col(set(norm_rows[0]), ["date", "start date"]) if norm_rows else None
    for i, row in enumerate(norm_rows, start=2):
        if date_col:
            if row.get(date_col) and parse_date(row[date_col]) is None:
                bad_date += 1
        for metric, alias_list in aliases.items():
            col = _pick_col(set(row), alias_list)
            if not col or not row.get(col):
                continue
            try:
                val = parse_number(row[col])
            except ValueError:
                bad_num.append({"row": i, "column": col, "value": row[col][:50]})
                continue
            if val is not None and val < 0:
                negative_hits.append({"row": i, "column": col, "value": row[col][:50]})
    if bad_num:
        findings.append(Finding("QF-03", "error", f"数值列存在非数值 {len(bad_num)} 处", raw.id, {"items": bad_num[:20]}))
    if bad_date:
        findings.append(Finding("QF-03", "error", f"日期解析失败 {bad_date} 行", raw.id, {"column": date_col}))
    if negative_hits:
        findings.append(Finding("QF-03", "error", f"度量列存在负值 {len(negative_hits)} 处", raw.id, {"items": negative_hits[:20]}))

    # QF-04：日期范围 gap（仅多日报表；区间型 start/end 报表跳过逐日 gap）
    if date_col == "date" and norm_rows:
        dates = sorted(
            d.isoformat()
            for d in (parse_date(r.get("date", "")) for r in norm_rows)
            if d is not None
        )
        if dates:
            start, end = dates[0], dates[-1]
            from datetime import date as _date

            d0 = _date.fromisoformat(start)
            d1 = _date.fromisoformat(end)
            expected = (d1 - d0).days + 1
            if expected > 1 and len(set(dates)) < expected:
                gaps = expected - len(set(dates))
                findings.append(
                    Finding("QF-04", "warn", f"日期范围 {start}~{end} 内缺 {gaps} 天", raw.id, {"start": start, "end": end, "missing_days": gaps})
                )

    # QF-05：币种（有 currency 列才检查；无列不虚构）
    if norm_rows:
        cur_col = _pick_col(set(norm_rows[0]), ["currency", "币种"])
        if cur_col:
            currencies = {r.get(cur_col, "").strip().upper() for r in norm_rows if r.get(cur_col)}
            currencies.discard("")
            if len(currencies) > 1:
                findings.append(Finding("QF-05", "error", f"批内多币种：{sorted(currencies)}", raw.id, {}))
            elif currencies and raw.marketplace and raw.marketplace.upper() == "UK" and currencies != {"GBP"}:
                findings.append(Finding("QF-05", "error", f"币种 {sorted(currencies)} 与 UK 站预期 GBP 不符", raw.id, {}))

    return findings


def check_batch_caliber(session: Session, batch_id: str) -> list[Finding]:
    """QF-06：同批混归因窗 / 混广告类型（导入后、状态机判定前）。"""
    findings: list[Finding] = []
    raws = session.scalars(
        select(RawDataset).where(RawDataset.batch_id == batch_id)
    ).all()
    windows = {r.attribution_window_days for r in raws if r.attribution_window_days}
    types = {r.ad_product_type for r in raws if r.ad_product_type and r.ad_product_type != "unknown"}
    if len(windows) > 1:
        findings.append(
            Finding("QF-06", "error", f"同批混归因窗：{sorted(windows)}", None, {"windows": sorted(windows)})
        )
    if len(types) > 1:
        findings.append(
            Finding("QF-06", "error", f"同批混广告类型：{sorted(types)}", None, {"ad_product_types": sorted(types)})
        )
    return findings


def check_supersede(session: Session, raw: RawDataset) -> Finding | None:
    """QF-08：同 report_type + 同日期范围、不同 hash 的既有登记 → 旧记录转 superseded。

    返回 QF-08 warn（若发生 supersede），旧数据不删（D6）。
    """
    if not raw.report_type or raw.report_type == "unknown":
        return None
    candidates = session.scalars(
        select(RawDataset).where(
            RawDataset.report_type == raw.report_type,
            RawDataset.data_start_date == raw.data_start_date,
            RawDataset.data_end_date == raw.data_end_date,
            RawDataset.file_hash != raw.file_hash,
            RawDataset.status.in_(["identified", "normalized"]),
        )
    ).all()
    superseded = []
    for old in candidates:
        old.status = "superseded"  # 过程字段更新（迁移已放行）
        old.superseded_by = raw.id
        superseded.append({"old_id": old.id, "old_hash": old.file_hash})
    if superseded:
        return Finding(
            "QF-08",
            "warn",
            f"检测到同类型同范围新版本，{len(superseded)} 条旧登记转 superseded（保留不删）",
            raw.id,
            {"superseded": superseded, "new_id": raw.id},
        )
    return None
