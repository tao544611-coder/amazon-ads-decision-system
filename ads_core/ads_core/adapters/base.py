# -*- coding: utf-8 -*-
"""源适配器接口（D11：源适配器 → canonical 统一层 → 事实层）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..ingest.reader import TableData
from ..storage.models import RawDataset


@dataclass
class CanonicalRow:
    """canonical 统一行（写入 can_* 表前的中间形态）。"""

    report_type: str
    date: str
    marketplace: str
    currency: str
    ad_product_type: str
    campaign_id: str
    campaign_name: str | None = None
    campaign_status: str | None = None
    portfolio_name: str | None = None
    impressions: int | None = None
    clicks: int | None = None
    spend: float | None = None
    orders: int | None = None
    sales: float | None = None
    units: int | None = None
    adgroup_id: str | None = None
    adgroup_name: str | None = None
    target_id: str | None = None
    target_text: str | None = None
    match_type: str | None = None
    search_term: str | None = None
    placement: str | None = None
    advertised_asin: str | None = None
    advertised_sku: str | None = None
    purchased_asin: str | None = None
    attribution_window_days: int | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    lineage: dict[str, Any] = field(default_factory=dict)


class AdapterError(Exception):
    """适配失败：结构不符合预期。"""


class SourceAdapter:
    """适配器基类：transform 源表 → CanonicalRow 列表。"""

    source_system: str = "unknown"

    def supports(self, raw: RawDataset) -> bool:
        return raw.report_type is not None and raw.report_type != "unknown"

    def transform(self, raw: RawDataset, table: TableData) -> list[CanonicalRow]:
        raise NotImplementedError
