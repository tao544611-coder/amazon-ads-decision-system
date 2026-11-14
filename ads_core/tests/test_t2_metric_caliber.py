# -*- coding: utf-8 -*-
"""T2 · 口径一致性测试（§7）：混广告类型 / 混归因窗 / 缺 spend / 指标复算。"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from ads_core.canonical import build_canonical
from ads_core.facts import build_facts
from ads_core.storage import (
    ImportBatch,
    MetricDaily,
    session_scope,
)

from .conftest import HEADERS, make_report, sample_campaign_rows

DATES = ["2026-09-01", "2026-09-02"]


def _to_complete(env, result):
    if result.status == "needs_confirm":
        with session_scope() as s:
            b = s.get(ImportBatch, result.batch_id)
            b.status = "complete"
            s.commit()


def test_t2_1_mixed_ad_product_type_rejected(env):
    """同批混广告类型（SP+SB 两文件）：QF-06 error → failed，不进 canonical。"""
    from ads_core.ingest import ingest_file

    f_sp = make_report(env / "sp.csv", "campaign", sample_campaign_rows(DATES))
    f_sb = make_report(env / "sb.csv", "campaign", sample_campaign_rows(DATES, "100000000000002"))
    with session_scope() as s:
        r_sp = ingest_file(s, f_sp, ad_product_type="sp", defer_finalize=True)
        r_sb = ingest_file(s, f_sb, ad_product_type="sb", batch_id=r_sp.batch_id)
        s.commit()
    assert r_sb.status == "failed"
    from ads_core.storage import CanCampaignDaily, QualityFinding

    with session_scope() as s:
        codes = [
            f.check_code
            for f in s.scalars(
                select(QualityFinding).where(QualityFinding.batch_id == r_sb.batch_id)
            ).all()
        ]
        assert "QF-06" in codes
        assert len(s.scalars(select(CanCampaignDaily)).all()) == 0


def test_t2_2_mixed_attribution_window_rejected(env):
    """同批混归因窗（7d 报表 + 14d 报表）：QF-06 error。"""
    import csv

    from ads_core.ingest import ingest_file

    f1 = make_report(env / "w7.csv", "campaign", sample_campaign_rows(DATES))

    rows14 = sample_campaign_rows(DATES, "100000000000003")
    headers = [h.replace("7 Day", "14 Day") for h in HEADERS["campaign"]]
    f2 = env / "w14.csv"
    with f2.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(headers)
        for row in rows14:
            row14 = {h.replace("7 Day", "14 Day"): v for h, v in row.items()}
            w.writerow([row14.get(h, "") for h in headers])

    with session_scope() as s:
        r1 = ingest_file(s, f1, defer_finalize=True)
        assert r1.attribution_window_days == 7
        r2 = ingest_file(s, f2, batch_id=r1.batch_id)
        s.commit()
    assert r2.attribution_window_days == 14
    assert r2.status == "failed"
    from ads_core.storage import QualityFinding

    with session_scope() as s:
        codes = [
            f.check_code
            for f in s.scalars(
                select(QualityFinding).where(QualityFinding.batch_id == r2.batch_id)
            ).all()
        ]
        assert "QF-06" in codes


def test_t2_3_missing_spend_computable_false(env, ingest):
    """缺 spend 列（人工 --type 指定）：进 facts 后 acos/cpc/roas/cpa 全部 computable=false。"""
    rows = sample_campaign_rows(["2026-09-01"])
    f = make_report(env / "nospend.csv", "campaign", rows, drop_headers=["Spend"])
    r = ingest(f, forced_type="campaign")
    assert r.status in {"complete", "needs_confirm"}
    _to_complete(env, r)
    with session_scope() as s:
        build_canonical(s, r.batch_id, actor="test")
        s.commit()
    with session_scope() as s:
        build_facts(s, batch_id=r.batch_id, grains=["campaign"], actor="test")
        s.commit()

    with session_scope() as s:
        row = s.scalars(
            select(MetricDaily).where(MetricDaily.grain == "campaign")
        ).first()
        assert row is not None
        comp = json.loads(row.computable_json)
        assert comp["acos"]["computable"] is False
        assert comp["cpc"]["computable"] is False
        assert comp["roas"]["computable"] is False
        assert comp["cpa"]["computable"] is False
        # ctr/cvr 不依赖 spend
        assert comp["ctr"]["computable"] is True
        assert comp["cvr"]["computable"] is True


def test_t2_4_recompute_consistency(env, ingest):
    """指标复算：metric_daily 全字段可由 batch_refs 指向的 canonical 行重算一致。"""
    dates = ["2026-09-01", "2026-09-02", "2026-09-03"]
    f = make_report(env / "camp.csv", "campaign", sample_campaign_rows(dates))
    r = ingest(f)
    _to_complete(env, r)
    with session_scope() as s:
        build_canonical(s, r.batch_id, actor="test")
        s.commit()
    with session_scope() as s:
        build_facts(s, batch_id=r.batch_id, grains=["campaign"], actor="test")
        s.commit()

    from ads_core.facts.metrics import compute_metrics

    with session_scope() as s:
        rows = s.scalars(select(MetricDaily).where(MetricDaily.grain == "campaign")).all()
        assert len(rows) == len(dates)
        for m in rows:
            refs = json.loads(m.batch_refs_json)
            # 从 canonical 重算
            agg = {"impressions": 0, "clicks": 0, "spend": 0.0, "orders": 0, "sales": 0.0}
            for ref in refs:
                from ads_core.storage import CanCampaignDaily

                can_rows = s.scalars(
                    select(CanCampaignDaily)
                    .where(
                        CanCampaignDaily.batch_id == ref["batch_id"],
                        CanCampaignDaily.raw_dataset_id == ref["raw_dataset_id"],
                        CanCampaignDaily.date == m.date,
                        CanCampaignDaily.campaign_id
                        == json.loads(m.grain_keys_json)["campaign_id"],
                    )
                ).all()
                for c in can_rows:
                    agg["impressions"] += c.impressions or 0
                    agg["clicks"] += c.clicks or 0
                    agg["spend"] += c.spend or 0.0
                    agg["orders"] += c.orders or 0
                    agg["sales"] += c.sales or 0.0
            recomputed = compute_metrics(
                agg["impressions"], agg["clicks"], agg["spend"], agg["orders"], agg["sales"]
            )
            assert abs((m.impressions or 0) - agg["impressions"]) < 1e-9
            assert abs((m.spend or 0) - agg["spend"]) < 1e-9
            for metric in ("ctr", "cpc", "cvr", "acos", "roas", "cpa"):
                a = getattr(m, metric)
                b = getattr(recomputed, metric)
                assert (a is None and b is None) or abs(a - b) < 1e-9, f"{metric}: {a} vs {b}"
