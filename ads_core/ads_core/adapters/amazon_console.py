# -*- coding: utf-8 -*-
"""亚马逊广告控制台 bulk 报表适配器 v1（英文列名）。

覆盖 7 类 SP 报表 + campaign 通用骨架；SB campaign 区间型报表按起始日挂载
（attrs 记录 report_granularity=range，原始列全量保留 attrs）。
"""

from __future__ import annotations

from typing import Any

from ..ingest.detector import load_fingerprints, normalize_header
from ..ingest.reader import TableData, parse_date, parse_number
from ..storage.models import RawDataset
from .base import AdapterError, CanonicalRow, SourceAdapter

COL = {
    "date": ["date"],
    "start_date": ["start date"],
    "end_date": ["end date"],
    # 校准（O-1）：后台下载报表无 Id 列时以 Name 作业务键，lineage 记录实际来源列
    "campaign_id": ["campaign id", "campaign name"],
    "campaign_name": ["campaign name"],
    "campaign_status": ["campaign status", "state"],
    "portfolio_name": ["portfolio name"],
    "adgroup_id": ["ad group id", "ad group name"],
    "adgroup_name": ["ad group name"],
    "target_id": ["keyword id"],
    "target_text": ["keyword or product targeting", "targeting"],
    "match_type": ["match type"],
    "search_term": ["customer search term"],
    "placement": ["placement"],
    "advertised_asin": ["advertised asin", "asin"],
    "advertised_sku": ["advertised sku"],
    "purchased_asin": ["purchased asin", "other asin"],
}


def _pick(norm_row: dict[str, str], keys: list[str]) -> tuple[str | None, str]:
    for k in keys:
        if k in norm_row:
            return k, norm_row[k]
    return None, ""


def _num(value: str) -> float | None:
    try:
        return parse_number(value)
    except ValueError as exc:
        raise AdapterError(f"数值列无法解析：{value!r}") from exc


def _int(value: str) -> int | None:
    v = _num(value)
    if v is None:
        return None
    return int(round(v))


class AmazonConsoleAdapter(SourceAdapter):
    source_system = "amazon_console"

    def supports(self, raw: RawDataset) -> bool:
        return (
            super().supports(raw)
            and (raw.source_system or self.source_system) == self.source_system
        )

    def transform(self, raw: RawDataset, table: TableData) -> list[CanonicalRow]:
        if raw.report_type is None or raw.report_type == "unknown":
            raise AdapterError("报表类型 unknown，无法适配")
        if len(table) == 0:
            raise AdapterError("数据体为空")

        fp = load_fingerprints()
        aliases = {k: [normalize_header(a) for a in v] for k, v in fp["metric_aliases"].items()}
        rows: list[CanonicalRow] = []

        for raw_row in table.rows:
            norm = {normalize_header(k): v for k, v in raw_row.items()}
            date_col, date_val = _pick(norm, COL["date"])
            range_granularity = False
            if date_col is None or not date_val:
                date_col, date_val = _pick(norm, COL["start_date"])
                range_granularity = True
            parsed = parse_date(date_val) if date_val else None
            if parsed is None:
                raise AdapterError(f"日期列无法解析：{date_val!r}")

            _, campaign_id = _pick(norm, COL["campaign_id"])
            if not campaign_id:
                raise AdapterError("缺 campaign id")

            row = CanonicalRow(
                report_type=raw.report_type or "unknown",
                date=parsed.isoformat(),
                marketplace=raw.marketplace or "UK",
                currency=raw.currency or "GBP",
                ad_product_type=raw.ad_product_type or "sp",
                campaign_id=campaign_id.strip(),
            )
            row.campaign_name = _pick(norm, COL["campaign_name"])[1] or None
            row.campaign_status = _pick(norm, COL["campaign_status"])[1] or None
            row.portfolio_name = _pick(norm, COL["portfolio_name"])[1] or None
            row.adgroup_id = _pick(norm, COL["adgroup_id"])[1] or None
            row.adgroup_name = _pick(norm, COL["adgroup_name"])[1] or None
            # 校准：后台报表普遍无 Keyword Id 列 → target_id 以 targeting 文本兜底，
            # 保证 canonical UNIQUE(batch,date,campaign,adgroup,target_id) 生效
            row.target_text = _pick(norm, COL["target_text"])[1] or None
            row.target_id = _pick(norm, COL["target_id"])[1] or row.target_text
            row.match_type = _pick(norm, COL["match_type"])[1] or None
            row.search_term = _pick(norm, COL["search_term"])[1] or None
            row.placement = _pick(norm, COL["placement"])[1] or None
            row.advertised_asin = _pick(norm, COL["advertised_asin"])[1] or None
            row.advertised_sku = _pick(norm, COL["advertised_sku"])[1] or None
            row.purchased_asin = _pick(norm, COL["purchased_asin"])[1] or None
            row.attribution_window_days = raw.attribution_window_days

            col_map: dict[str, str] = {"date": date_col, "campaign_id": "campaign id"}
            for metric, alias_list in aliases.items():
                col = next((a for a in alias_list if a in norm), None)
                if col is None:
                    continue
                val = norm[col]
                if not val:
                    continue
                col_map[metric] = col
                if metric in {"impressions", "clicks", "orders", "units"}:
                    setattr(row, metric, _int(val))
                else:
                    setattr(row, metric, _num(val))

            attrs: dict[str, Any] = dict(raw_row)
            if range_granularity:
                attrs["__report_granularity"] = "range"
                _, end_v = _pick(norm, COL["end_date"])
                if end_v:
                    attrs["__range_end_date"] = end_v
            row.attrs = attrs
            row.lineage = {
                "source_system": self.source_system,
                "source_col_map": col_map,
            }
            rows.append(row)

        return rows
