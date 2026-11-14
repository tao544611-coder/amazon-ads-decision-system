# -*- coding: utf-8 -*-
"""T3 · 成熟度测试（§7 / HR-01/02/04）。"""

from __future__ import annotations

import json
from datetime import date, timedelta

from sqlalchemy import select

from ads_core.canonical import build_canonical
from ads_core.facts import build_facts
from ads_core.storage import ImportBatch, MetricDaily, QualityFinding, session_scope

from .conftest import make_report, sample_campaign_rows

TODAY = date(2026, 11, 14)


def _iso(d: date) -> str:
    return d.isoformat()


def _ingest_and_build(env, ingest, tag: str, rows, ad_product_type="sp", data_as_of=None):
    f = make_report(env / f"m_{tag}.csv", "campaign", rows)
    r = ingest(f, ad_product_type=ad_product_type, data_as_of=data_as_of)
    if r.status == "needs_confirm":
        with session_scope() as s:
            b = s.get(ImportBatch, r.batch_id)
            b.status = "complete"
            s.commit()
    with session_scope() as s:
        build_canonical(s, r.batch_id, actor="test")
        s.commit()
    with session_scope() as s:
        build_facts(s, batch_id=r.batch_id, grains=["campaign"], actor="test")
        s.commit()
    return r


def test_t3_1_sp_maturity_threshold(env, ingest):
    """SP：lag<7 → immature_observation；lag≥7 → mature（HR-01）。"""
    as_of = _iso(TODAY)
    rows = sample_campaign_rows(
        [
            _iso(TODAY - timedelta(days=3)),   # SP lag=3 <7 → 未成熟
            _iso(TODAY - timedelta(days=6)),   # lag=6 <7 → 未成熟
            _iso(TODAY - timedelta(days=7)),   # lag=7 → 成熟
            _iso(TODAY - timedelta(days=30)),  # 成熟
        ],
        campaign_id="200000000000001",
    )
    _ingest_and_build(env, ingest, "sp", rows, ad_product_type="sp", data_as_of=as_of)
    with session_scope() as s:
        ms = s.scalars(
            select(MetricDaily).where(MetricDaily.grain == "campaign").order_by(MetricDaily.date)
        ).all()
        by_date = {m.date: m.maturity for m in ms}
    assert by_date[_iso(TODAY - timedelta(days=3))] == "immature_observation"
    assert by_date[_iso(TODAY - timedelta(days=6))] == "immature_observation"
    assert by_date[_iso(TODAY - timedelta(days=7))] == "mature"
    assert by_date[_iso(TODAY - timedelta(days=30))] == "mature"


def test_t3_2_sb_stricter_threshold(env, ingest):
    """SB：lag=10 ≥7 但 <14 → 仍 immature（SB 阈 14，HR-01/02）。"""
    as_of = _iso(TODAY)
    rows = sample_campaign_rows(
        [
            _iso(TODAY - timedelta(days=10)),
            _iso(TODAY - timedelta(days=14)),
        ],
        campaign_id="300000000000001",
    )
    _ingest_and_build(env, ingest, "sb", rows, ad_product_type="sb", data_as_of=as_of)
    with session_scope() as s:
        ms = s.scalars(select(MetricDaily).where(MetricDaily.grain == "campaign")).all()
        by_date = {m.date: m.maturity for m in ms}
    assert by_date[_iso(TODAY - timedelta(days=10))] == "immature_observation"
    assert by_date[_iso(TODAY - timedelta(days=14))] == "mature"


def test_t3_3_t2_offset_does_not_change_maturity(env, ingest):
    """T-2：data_as_of=today-2，date=today-9 的 SP lag=7 → mature（HR-04：T-2 只挡延迟）。"""
    as_of = _iso(TODAY - timedelta(days=2))
    rows = sample_campaign_rows(
        [_iso(TODAY - timedelta(days=9))], campaign_id="400000000000001"
    )
    _ingest_and_build(env, ingest, "t2", rows, ad_product_type="sp", data_as_of=as_of)
    with session_scope() as s:
        m = s.scalars(select(MetricDaily).where(MetricDaily.grain == "campaign")).first()
    assert m.maturity == "mature"


def test_t3_4_qf10_overview_generated(env, ingest):
    """QF-10：facts 构建产出未成熟占比 info finding（不阻断）。"""
    as_of = _iso(TODAY)
    rows = sample_campaign_rows(
        [_iso(TODAY - timedelta(days=1)), _iso(TODAY - timedelta(days=40))],
        campaign_id="500000000000001",
    )
    r = _ingest_and_build(env, ingest, "qf10", rows, ad_product_type="sp", data_as_of=as_of)
    with session_scope() as s:
        qf = s.scalars(
            select(QualityFinding).where(
                QualityFinding.batch_id == r.batch_id, QualityFinding.check_code == "QF-10"
            )
        ).all()
        assert qf, "QF-10 info finding 未生成"
        assert qf[0].severity == "info"
        details = json.loads(qf[0].details_json)
        assert details["total_rows"] == 2
        assert details["immature_rows"] == 1
