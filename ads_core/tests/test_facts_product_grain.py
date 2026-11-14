# -*- coding: utf-8 -*-
"""product 粒度事实构建测试：campaign_map 双键映射（Id 或活动名）。"""

from __future__ import annotations

from sqlalchemy import select

from ads_core.canonical import build_canonical
from ads_core.facts import build_facts
from ads_core.profile import map_campaign, upsert_product
from ads_core.storage import ImportBatch, MetricDaily, session_scope

from .conftest import make_report, sample_campaign_rows


def _finish(env, r):
    if r.status == "needs_confirm":
        with session_scope() as s:
            s.get(ImportBatch, r.batch_id).status = "complete"
            s.commit()


def test_product_grain_maps_by_campaign_name(env, ingest):
    """报表无 Campaign Id（键=活动名）时，campaign_map 按活动名映射 → product 粒度出数。"""
    rows = sample_campaign_rows(["2026-09-01", "2026-09-02"], campaign_id="WARM-BOX-Auto")
    for row in rows:
        row["Campaign Name"] = "GJHIUBKNGT-AZDAuto"  # 模拟真实报表：campaign_id 列实为活动名
    f = make_report(env / "camp_name_key.csv", "campaign", rows)
    r = ingest(f)
    _finish(env, r)

    with session_scope() as s:
        p = upsert_product(s, sku="6978", actor="test")
        s.commit()
        map_campaign(
            s, campaign_id="29437742665971", product_id=p.product_id, mapped_by="test",
            campaign_name="GJHIUBKNGT-AZDAuto", ad_product_type="sp",
        )
        s.commit()
        build_canonical(s, r.batch_id, actor="test")
        result = build_facts(s, grains=["campaign", "product"], actor="test")
        s.commit()

    assert result.by_grain.get("product", 0) == 2, "product 粒度应产出 2 行（每日一行）"
    with session_scope() as s:
        prod_rows = s.scalars(
            select(MetricDaily).where(MetricDaily.grain == "product")
        ).all()
        assert all(row.product_id == p.product_id for row in prod_rows)
        camp_rows = s.scalars(
            select(MetricDaily).where(MetricDaily.grain == "campaign")
        ).all()
        assert all(row.product_id == p.product_id for row in camp_rows), (
            "campaign 粒度行也应带上映射出的 product_id"
        )
        total = sum(row.spend or 0 for row in prod_rows)
        assert abs(total - (10.5 + 11.5)) < 1e-9
