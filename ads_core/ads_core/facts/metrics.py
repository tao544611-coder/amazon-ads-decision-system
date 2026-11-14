# -*- coding: utf-8 -*-
"""事实指标公式（设计 §4.1 · formula_version = methodology.version_no）。

分母为零/缺源 → computable=false + reason，绝不估算（PRD 4.5 红线）。
"""

from __future__ import annotations

from dataclasses import dataclass

BUSINESS_SOURCE_METRICS = ("organic_order_ratio", "tacos", "gross_profit", "ad_profit")


@dataclass
class MetricsResult:
    ctr: float | None = None
    cpc: float | None = None
    cvr: float | None = None
    acos: float | None = None
    roas: float | None = None
    cpa: float | None = None
    computable: dict[str, dict] | None = None


def _ratio(num: float | None, den: float | None, zero_reason: str) -> tuple[float | None, dict]:
    if den is None or num is None:
        return None, {"computable": False, "reason": "missing_source"}
    if den == 0:
        return None, {"computable": False, "reason": zero_reason}
    return num / den, {"computable": True}


def compute_metrics(
    impressions: int | None,
    clicks: int | None,
    spend: float | None,
    orders: int | None,
    sales: float | None,
) -> MetricsResult:
    res = MetricsResult()
    comp: dict[str, dict] = {}

    res.ctr, comp["ctr"] = _ratio(float(clicks or 0) if clicks is not None else None, float(impressions) if impressions is not None else None, "zero_impressions")
    res.cpc, comp["cpc"] = _ratio(spend, float(clicks) if clicks is not None else None, "zero_clicks")
    res.cvr, comp["cvr"] = _ratio(float(orders) if orders is not None else None, float(clicks) if clicks is not None else None, "zero_clicks")
    res.acos, comp["acos"] = _ratio(spend, sales, "zero_sales")
    res.roas, comp["roas"] = _ratio(sales, spend, "zero_spend")
    res.cpa, comp["cpa"] = _ratio(spend, float(orders) if orders is not None else None, "zero_orders")

    for name in BUSINESS_SOURCE_METRICS:
        comp[name] = {"computable": False, "reason": "missing_business_source"}

    res.computable = comp
    return res
