# -*- coding: utf-8 -*-
"""成熟度标记（HR-01/02/04，设计 §4.2）。

lag = data_as_of - date（报表原生日期）；
SP 阈值 7 天、SB/SD 14 天（取自方法论参数）；
T-2 只挡进报表延迟，不改变本式（HR-04）。
"""

from __future__ import annotations

from datetime import date


def maturity_for(
    row_date: str,
    ad_product_type: str,
    data_as_of: str,
    sp_min_days: int,
    sb_sd_min_days: int,
) -> str:
    lag = (date.fromisoformat(data_as_of) - date.fromisoformat(row_date)).days
    threshold = sb_sd_min_days if ad_product_type in {"sb", "sd"} else sp_min_days
    return "mature" if lag >= threshold else "immature_observation"


def maturity_strict_for_types(
    ad_product_types: set[str],
    sp_min_days: int,
    sb_sd_min_days: int,
) -> int:
    """HR-02：混合粒度含 SB/SD → 从严取更严阈值。返回应采用的阈值天数。"""
    if ad_product_types & {"sb", "sd"}:
        return max(sp_min_days, sb_sd_min_days)
    return sp_min_days
