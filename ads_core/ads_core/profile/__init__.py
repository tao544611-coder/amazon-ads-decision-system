# -*- coding: utf-8 -*-
"""profile：产品、档案版本（SCD-2）与 campaign 映射。"""

from .product import (
    ProductResult,
    map_campaign,
    update_profile,
    upsert_product,
)

__all__ = ["ProductResult", "map_campaign", "update_profile", "upsert_product"]
