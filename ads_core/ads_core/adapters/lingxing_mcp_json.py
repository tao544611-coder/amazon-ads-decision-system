# -*- coding: utf-8 -*-
"""领星 MCP JSON 响应适配器（D11 二期）。

数据形态：领星开放平台 MCP 只读接口的原始 JSON 响应（自动化落盘存档）。
本期覆盖「按日拉取」的日粒度行（如 ``ad_sp_campaign_report``：行含 ``report_date``）；
窗口聚合类 JSON（无任何日期字段，如 ``ad_campaign_product_report``）在行级被显式
拒绝——事实层 MetricDaily 为日粒度，缺日期不虚构（PRD 红线：不虚构数据），
拒绝信息中写明原因，供自动化侧改进拉取方式。

映射（行级，字段为领星 API 原名）：
- ``report_date`` → date（唯一日期键；缺失/不可解析 → AdapterError）
- ``campaign_id`` → campaign 业务键（领星 MCP 活动报表无 campaign_name 列，
  campaign_name 允许为空，归属靠 CampaignMap 按 campaign_id 登记）
- ``impressions``/``clicks``/``orders``/``units`` → 整型度量
- ``cost`` → spend、``sales`` → 浮型度量
- marketplace/currency：响应无此字段，取导入参数（raw.marketplace / raw.currency）
"""

from __future__ import annotations

from typing import Any

from ..ingest.detector import normalize_header
from ..ingest.reader import TableData, parse_date, parse_number
from ..storage.models import RawDataset
from .base import AdapterError, CanonicalRow, SourceAdapter

DATE_KEYS = ("report date", "date")          # 归一化后的键（report_date → report date）
INT_METRICS = ("impressions", "clicks", "orders", "units")
FLOAT_METRICS = ("cost", "sales")


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


class LingXingMcpJsonAdapter(SourceAdapter):
    source_system = "lingxing_mcp_json"

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

        rows: list[CanonicalRow] = []
        for raw_row in table.rows:
            norm = {normalize_header(k): v for k, v in raw_row.items()}

            # 日期：日粒度硬条件——窗口聚合 JSON 在此被明确拒绝（不虚构日期归属）
            date_val = next((norm[k] for k in DATE_KEYS if norm.get(k)), None)
            parsed = parse_date(date_val) if date_val else None
            if parsed is None:
                raise AdapterError(
                    "行缺日粒度日期（report_date/date 缺失或不可解析）：窗口聚合 JSON "
                    "不入事实层（PRD 红线：不虚构日期归属）；请改为按日拉取（如 "
                    "ad_sp_campaign_report 单日查询）"
                )

            _, campaign_id = next(
                ((k, norm[k]) for k in ("campaign id", "campaign name") if norm.get(k)),
                ("", ""),
            )
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
            row.campaign_name = norm.get("campaign name") or None
            row.campaign_status = norm.get("campaign state") or norm.get("state") or None

            for metric in INT_METRICS:
                if norm.get(metric):
                    setattr(row, metric, _int(norm[metric]))
            for metric in FLOAT_METRICS:
                target = "spend" if metric == "cost" else metric
                if norm.get(metric):
                    setattr(row, target, _num(norm[metric]))

            attrs: dict[str, Any] = dict(raw_row)
            row.attrs = attrs
            row.lineage = {
                "source_system": self.source_system,
                "source_col_map": {
                    "date": "report_date", "campaign_id": "campaign_id",
                    "spend": "cost", "sales": "sales",
                },
            }
            rows.append(row)
        return rows
