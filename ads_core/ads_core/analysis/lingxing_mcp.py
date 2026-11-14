# -*- coding: utf-8 -*-
"""领星 MCP 周检原始数据 → 分析上下文补充证据（D11 一期）。

定位（Gordon 2026-11-14 决议）：MCP 数据为周期聚合（meta.report_date 一周一条），
不进按日事实层/成熟度口径（HR-01/02），只作为分析上下文包的补充证据段
``lingxing_mcp_supplement``，供 AI 分析时对照周检口径。

数据源：``ADS_LINGXING_MCP_RAW``（默认 Amazon_Work/projects/auto-ads-check/data/raw）
每周目录含 ``ad_campaign_report.json``（店级活动聚合）与 ``<SKU组>/product_report_<ASIN>.json``
（本期/上期商品级）。加载只读、确定性；任何单周解析失败降级跳过，不阻断组装。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .. import config

# 周检 JSON 行中参与聚合/透出的字段（领星 API 英文字段，只取白名单，D9 原则）
_CAMPAIGN_FIELDS = ("name", "impressions", "clicks", "spends", "sales", "orders")
_PRODUCT_FIELDS = (
    "asin", "sku", "campaign_name", "ad_group_name",
    "impressions", "clicks", "spends", "sales", "orders",
)


def _parse_period(report_date: str) -> tuple[date, date] | None:
    """解析 meta.report_date（'2026-11-01 - 2026-11-07' 或单日）。"""
    parts = [p.strip() for p in str(report_date).split(" - ") if p.strip()]
    try:
        start = date.fromisoformat(parts[0])
        end = date.fromisoformat(parts[-1])
        return start, end
    except (ValueError, IndexError):
        return None


def _num(value: object) -> float | None:
    if value is None or value == "--" or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _row_fields(row: dict, fields: tuple[str, ...]) -> dict:
    out: dict = {}
    for f in fields:
        v = row.get(f)
        out[f] = _num(v) if f in {"impressions", "clicks", "spends", "sales", "orders"} else v
    return out


def _aggregate_campaigns(rows: list[dict]) -> dict:
    agg = {"impressions": 0, "clicks": 0, "spends": 0.0, "sales": 0.0, "orders": 0,
           "rows": len(rows), "named_rows": 0}
    for r in rows:
        if r.get("name"):
            agg["named_rows"] += 1
        for f in ("impressions", "clicks", "spends", "sales", "orders"):
            v = _num(r.get(f))
            if v is not None:
                agg[f] += v
    agg["spends"] = round(agg["spends"], 2)
    agg["sales"] = round(agg["sales"], 2)
    return agg


def _load_week(week_dir: Path, asins: set[str]) -> dict | None:
    """读单周目录；无 campaign 报表返回 None。"""
    camp_path = week_dir / "ad_campaign_report.json"
    if not camp_path.is_file():
        return None
    try:
        with camp_path.open(encoding="utf-8") as f:
            camp = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    meta = camp.get("meta") or {}
    period = _parse_period(meta.get("report_date") or "")
    if period is None:
        return None

    rows = camp.get("data") or []
    if not isinstance(rows, list):
        rows = []

    matched_products: list[dict] = []
    product_files = 0
    for sub in sorted(week_dir.iterdir()):
        if not sub.is_dir():
            continue
        for pf in sorted(sub.glob("product_report_*.json")):
            try:
                with pf.open(encoding="utf-8") as f:
                    pr = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            product_files += 1
            for phase in ("current", "previous"):
                for row in pr.get(phase) or []:
                    if isinstance(row, dict) and row.get("asin") in asins:
                        entry = _row_fields(row, _PRODUCT_FIELDS)
                        entry["phase"] = phase
                        entry["source_file"] = pf.name
                        matched_products.append(entry)

    return {
        "week": week_dir.name,
        "report_date": meta.get("report_date"),
        "period_start": period[0].isoformat(),
        "period_end": period[1].isoformat(),
        "pulled_at": meta.get("pulled_at"),
        "source": meta.get("source") or "lingxing MCP ad_campaign_report（只读）",
        "store_campaign_aggregate": _aggregate_campaigns(rows),
        "matched_products": matched_products,
        "product_files_scanned": product_files,
    }


def collect_lingxing_supplement(
    date_from: str,
    date_to: str,
    asins: set[str],
) -> dict:
    """扫描周检 MCP 原始数据，取窗口重叠周的店级聚合 + 匹配 ASIN 的商品行。

    返回固定结构；数据源缺失/为空时 status=unavailable 并说明原因（不虚构、不阻断）。
    """
    root = config.lingxing_mcp_raw_root()
    if not root.is_dir():
        return {"status": "unavailable", "reason": f"原始数据目录不存在：{root}"}

    lo = date.fromisoformat(date_from)
    hi = date.fromisoformat(date_to)
    weeks: list[dict] = []
    for week_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        week = _load_week(week_dir, asins)
        if week is None:
            continue
        if date.fromisoformat(week["period_end"]) < lo or date.fromisoformat(week["period_start"]) > hi:
            continue
        weeks.append(week)

    if not weeks:
        return {
            "status": "unavailable",
            "reason": f"窗口 {date_from}~{date_to} 无重叠周数据（扫描目录：{root}）",
        }

    return {
        "status": "ok",
        "note": (
            "领星 MCP 周检数据为周期聚合口径（与按日事实层不同源），"
            "仅作对照证据，不参与成熟度/事实层计算（HR-01/02 不适用）"
        ),
        "matched_asins": sorted(asins),
        "weeks": weeks,
    }
