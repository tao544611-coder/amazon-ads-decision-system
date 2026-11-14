# -*- coding: utf-8 -*-
"""产品档案（SCD-2）与业务事件测试（§7）。"""

from __future__ import annotations

import json
from datetime import date

from sqlalchemy import select

from ads_core.events import add_business_event, events_in_window
from ads_core.profile import map_campaign, update_profile, upsert_product
from ads_core.storage import CampaignMap, ProductProfileVersion, Product, session_scope


def _payload(target_acos=0.25):
    return {
        "A_basic": {"asin": "B0TEST0001", "title": "测试产品"},
        "B_operations": {"target_acos": target_acos, "price": 19.99},
        "E_advertising": {"main_campaigns": [{"campaign_id": "123", "campaign_name": "Core Exact"}]},
    }


def test_profile_scd2_versions_and_diff(env):
    with session_scope() as s:
        p = upsert_product(s, sku="6978", asin="B0TEST0001", actor="test")
        s.commit()
        product_id = p.product_id

        v1 = update_profile(s, product_id, _payload(), reason="初始建档", changed_by="Gordon")
        s.commit()
        v2 = update_profile(s, product_id, _payload(target_acos=0.20), reason="调目标 ACOS 25%→20%", changed_by="Gordon")
        s.commit()
        v3 = update_profile(s, product_id, _payload(target_acos=0.20), reason="补充备注再版", changed_by="运营A")
        s.commit()

    with session_scope() as s:
        versions = s.scalars(
            select(ProductProfileVersion)
            .where(ProductProfileVersion.product_id == product_id)
            .order_by(ProductProfileVersion.version_no)
        ).all()
        assert [v.version_no for v in versions] == [1, 2, 3]
        diff2 = json.loads(versions[1].diff_json)
        assert diff2["changed"], "第 2 版应包含与第 1 版的差异"
        assert diff2["changed"]["B_operations"]["to"]["target_acos"] == 0.20
        diff3 = json.loads(versions[2].diff_json)
        assert not diff3["changed"] and not diff3["added"] and not diff3["removed"], "内容相同的第 3 版 diff 为空"


def test_campaign_map_and_product_grain_link(env):
    with session_scope() as s:
        p = upsert_product(s, sku="6979", actor="test")
        s.commit()
        entry = map_campaign(
            s, campaign_id="999888777666", product_id=p.product_id,
            mapped_by="Gordon", campaign_name="6979 Phrase", ad_product_type="sp",
        )
        s.commit()
    with session_scope() as s:
        rows = s.scalars(select(CampaignMap)).all()
        assert len(rows) == 1
        assert rows[0].campaign_id == "999888777666"
        assert rows[0].product_id == p.product_id


def test_business_event_window_query(env):
    with session_scope() as s:
        p = upsert_product(s, sku="6978", actor="test")
        s.commit()
        e1 = add_business_event(
            s, event_type="deal", starts_at="2026-11-01", ends_at="2026-11-07",
            description="黑五 Deal", recorded_by="Gordon", product_id=p.product_id,
        )
        e2 = add_business_event(
            s, event_type="stock_change", starts_at="2026-11-20",
            description="补货 500 件", recorded_by="Gordon", product_id=p.product_id,
        )
        s.commit()

    with session_scope() as s:
        hit = events_in_window(s, "2026-11-01", "2026-11-15")
        ids = [e.id for e in hit]
        assert e1.id in ids
        assert e2.id not in ids
        hit2 = events_in_window(s, "2026-11-05", "2026-11-25")
        assert e1.id in [e.id for e in hit2]  # 区间重叠
        assert e2.id in [e.id for e in hit2]


def test_event_requires_product_or_asin(env):
    with session_scope() as s:
        try:
            add_business_event(
                s, event_type="other", starts_at="2026-11-01",
                description="无主体事件", recorded_by="test",
            )
            raise AssertionError("product_id/asin 全空应报错")
        except ValueError:
            pass
        s.rollback()


def test_keyword_rank_snapshot(env):
    from ads_core.events import add_keyword_rank

    with session_scope() as s:
        p = upsert_product(s, sku="6978", actor="test")
        s.commit()
        snap = add_keyword_rank(
            s, product_id=p.product_id, keyword="yoga mat",
            captured_on="2026-11-14", recorded_by="Gordon", organic_rank=3,
        )
        s.commit()
    with session_scope() as s:
        from ads_core.storage import KeywordRankSnapshot

        rows = s.scalars(select(KeywordRankSnapshot)).all()
        assert rows[0].organic_rank == 3
        assert rows[0].source == "manual"
        assert rows[0].id == snap.id
