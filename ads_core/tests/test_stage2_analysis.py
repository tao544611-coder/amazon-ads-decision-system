# -*- coding: utf-8 -*-
"""阶段二测试：T4 产品背景 + T5 决策依据 + 校验器硬规则 + 成熟度隔离（设计 §4）。"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from ads_core.analysis import (
    ContextError,
    assemble_analysis_context,
    validate_proposals,
)
from ads_core.canonical import build_canonical
from ads_core.events import add_business_event, add_keyword_rank
from ads_core.facts import build_facts
from ads_core.ingest.importer import ingest_file
from ads_core.profile import map_campaign, update_profile, upsert_product
from ads_core.storage import (
    AnalysisRun,
    Evidence,
    ImportBatch,
    MetricDaily,
    session_scope,
)

from .conftest import make_report, sample_campaign_rows

TODAY = date(2026, 11, 14)
AS_OF = TODAY.isoformat()
MATURE_DAY = (TODAY - timedelta(days=10)).isoformat()   # SP lag=10 ≥7 成熟
IMMATURE_DAY = (TODAY - timedelta(days=2)).isoformat()  # lag=2 未成熟
DATE_FROM = (TODAY - timedelta(days=30)).isoformat()
DATE_TO = TODAY.isoformat()
CAMPAIGN_NAME = "TESTNGT-AZDAuto"
LOW_CLICK_CAMPAIGN = "TESTNGT-ACZOffice Phrase"      # clicks=8 < 20（VR-04 素材）


@pytest.fixture()
def stage2_env(env):
    """产品+档案+映射+成熟/未成熟事实行+低点击行+活跃事件+两次排名快照。"""
    with session_scope() as s:
        p = upsert_product(s, sku="6978", asin="B0TEST0001", actor="test")
        s.commit()
        update_profile(
            s, p.product_id,
            {"A_basic": {"asin": "B0TEST0001"}, "B_operations": {"target_acos": 0.25}},
            reason="v1", changed_by="test",
        )
        s.commit()
        map_campaign(s, campaign_id="111111111111", product_id=p.product_id,
                     mapped_by="test", campaign_name=CAMPAIGN_NAME, ad_product_type="sp")
        map_campaign(s, campaign_id="222222222222", product_id=p.product_id,
                     mapped_by="test", campaign_name=LOW_CLICK_CAMPAIGN, ad_product_type="sp")
        s.commit()
        add_business_event(
            s, event_type="deal", starts_at=MATURE_DAY, ends_at=IMMATURE_DAY,
            description="黑五秒杀", recorded_by="Gordon", product_id=p.product_id,
        )
        s.commit()
        add_keyword_rank(s, product_id=p.product_id, keyword="heated lunch box",
                         captured_on=(TODAY - timedelta(days=20)).isoformat(),
                         recorded_by="Gordon", organic_rank=8)
        add_keyword_rank(s, product_id=p.product_id, keyword="heated lunch box",
                         captured_on=(TODAY - timedelta(days=3)).isoformat(),
                         recorded_by="Gordon", organic_rank=5)
        s.commit()

    rows = sample_campaign_rows([MATURE_DAY, IMMATURE_DAY], campaign_id="111111111111")
    for r in rows:
        r["Campaign Name"] = CAMPAIGN_NAME
    low_rows = sample_campaign_rows([MATURE_DAY], campaign_id="222222222222")
    for r in low_rows:
        r["Campaign Name"] = LOW_CLICK_CAMPAIGN
        r["Clicks"] = "8"  # < min_clicks_for_bid_action(20)
    f = make_report(env / "s2_camp.csv", "campaign", rows)
    f2 = make_report(env / "s2_low.csv", "campaign", low_rows)
    with session_scope() as s:
        r = ingest_file(s, f, data_as_of=AS_OF, defer_finalize=True)
        r2 = ingest_file(s, f2, data_as_of=AS_OF, batch_id=r.batch_id)
        s.commit()
    assert r2.status in {"complete", "needs_confirm"}
    if r2.status == "needs_confirm":
        with session_scope() as s:
            s.get(ImportBatch, r2.batch_id).status = "complete"
            s.commit()
    with session_scope() as s:
        build_canonical(s, r2.batch_id, actor="test")
        s.commit()
    with session_scope() as s:
        build_facts(s, grains=["campaign", "product"], actor="test")
        s.commit()
    return {"batch_id": r2.batch_id}


def _mature_fact_id() -> str:
    """主活动（clicks≥20）MATURE_DAY 行的 fact_id。"""
    with session_scope() as s:
        m = s.scalars(
            select(MetricDaily).where(
                MetricDaily.grain == "campaign", MetricDaily.date == MATURE_DAY,
                MetricDaily.clicks >= 20,
            )
        ).first()
        assert m is not None
        return m.id


def _immature_fact_id() -> str:
    with session_scope() as s:
        m = s.scalars(
            select(MetricDaily).where(
                MetricDaily.grain == "campaign", MetricDaily.date == IMMATURE_DAY
            )
        ).first()
        assert m is not None
        return m.id


def _event_id() -> str:
    from ads_core.storage import BusinessEvent

    with session_scope() as s:
        e = s.scalars(select(BusinessEvent)).first()
        return e.id


def _make_context(stage2_env) -> str:
    with session_scope() as s:
        result = assemble_analysis_context(
            s, sku="6978", date_from=DATE_FROM, date_to=DATE_TO, actor="test",
        )
        s.commit()
    assert result.status == "context_ready"
    return result.run_id


def _base_proposal(**overrides) -> dict:
    p = {
        "sku": "6978",
        "target": CAMPAIGN_NAME,
        "current_performance": "ACOS 30%，点击 41",
        "problem": "ACOS 高于目标 25%",
        "fact_evidence_ids": [_mature_fact_id()],
        "event_evidence_ids": [_event_id()],
        "reasoning": "成熟期数据显示 ACOS 偏高（依据 HR-01 已确认成熟），事件影响已在 risks 说明",
        "action": "adjust_bid",
        "adjustment": "-10%",
        "new_bid": 0.60,
        "expected_effect": "ACOS 向 25% 收敛",
        "risks": "黑五秒杀窗口流量结构异常，可能低估转化",
        "observation_window": "7 天或下期周检",
        "acceptance_criteria": "ACOS ≤ 25% 或花费持平",
        "needs_approval": True,
    }
    p.update(overrides)
    return p


def _validate(run_id: str, proposals: list[dict]) -> list:
    with session_scope() as s:
        verdicts = validate_proposals(s, run_id, proposals, actor="test")
        s.commit()
    return verdicts


# ================================================================ T4


def test_t4_1_context_contains_events_and_ranks(stage2_env):
    run_id = _make_context(stage2_env)
    with session_scope() as s:
        run = s.get(AnalysisRun, run_id)
        ctx = json.loads(run.context_json)
        flags = json.loads(run.context_flags_json)
    # 业务事件在包内且 flags 置位
    assert len(ctx["business_events"]) == 1
    assert ctx["business_events"][0]["description"] == "黑五秒杀"
    assert len(flags["confounding_events"]) == 1
    # 排名两次快照对比：8→5 = 提升
    ranks = ctx["keyword_ranks"]
    assert ranks and ranks[0]["keyword"] == "heated lunch box"
    assert ranks[0]["current"]["organic_rank"] == 5
    assert ranks[0]["previous"]["organic_rank"] == 8
    assert ranks[0]["delta_rank"] == 3
    assert ranks[0]["trend"] == "up"
    # 档案与方法论在包内（七步齐全）
    assert ctx["product_profile"]["B_operations"]["target_acos"] == 0.25
    assert any(r["rule_code"] == "HR-01" for r in ctx["methodology"]["hard_rules"])
    assert ctx["proposal_schema"]["required_fields"][0] == "sku"


def test_t4_2_single_metric_guard_vr05(stage2_env):
    """活跃事件窗口内的建议未引用事件 → VR-05 拒绝（T4 引擎侧闸门）。"""
    run_id = _make_context(stage2_env)
    verdicts = _validate(run_id, [_base_proposal(event_evidence_ids=[])])
    assert verdicts[0].verdict == "rejected_hard_rule"
    assert any(v["code"] == "VR-05" for v in verdicts[0].violations)


def test_t4_3_context_maturity_isolation(stage2_env):
    """未成熟行带标注；comparison 仅使用成熟行。"""
    run_id = _make_context(stage2_env)
    with session_scope() as s:
        ctx = json.loads(s.get(AnalysisRun, run_id).context_json)
    maturities = {f["maturity"] for f in ctx["facts"]["campaign"]}
    assert maturities == {"mature", "immature_observation"}
    comp = ctx["facts"]["comparison_campaign_mature_only"]
    # 窗口 D-30~D0 中点 = D-15：成熟日（D-10）落在后半段，未成熟日（D-2）被排除
    # （comparison 只收成熟行，maturity 过滤在段内聚合时生效）
    assert comp["second_half"]["rows"] == 2
    assert comp["first_half"]["rows"] == 0
    flags = json.loads(s.get(AnalysisRun, run_id).context_flags_json)
    assert flags["immature_ratio"] > 0


# ================================================================ 校验器硬规则


def test_validator_passes_valid_proposal(stage2_env):
    run_id = _make_context(stage2_env)
    verdicts = _validate(run_id, [_base_proposal()])
    assert verdicts[0].verdict == "passed", verdicts[0].violations
    with session_scope() as s:
        run = s.get(AnalysisRun, run_id)
        assert run.status == "proposals_validated"


def test_validator_vr01_immature_conversion_evidence(stage2_env):
    run_id = _make_context(stage2_env)
    verdicts = _validate(run_id, [_base_proposal(fact_evidence_ids=[_immature_fact_id()])])
    assert verdicts[0].verdict == "rejected_hard_rule"
    assert any(v["code"] == "VR-01" for v in verdicts[0].violations)


def test_validator_vr02_adjustment_over_limit(stage2_env):
    run_id = _make_context(stage2_env)
    verdicts = _validate(run_id, [_base_proposal(adjustment="-20%")])
    assert any(v["code"] == "VR-02" for v in verdicts[0].violations)


def test_validator_vr03_bid_out_of_bounds(stage2_env):
    run_id = _make_context(stage2_env)
    verdicts = _validate(run_id, [_base_proposal(new_bid=3.00)])
    assert any(v["code"] == "VR-03" for v in verdicts[0].violations)


def test_validator_vr04_insufficient_clicks(stage2_env):
    """证据点击合计 < 20 且非观察类 → 拒（方法论：点击<20 → 继续观察）。"""
    with session_scope() as s:
        low = s.scalars(
            select(MetricDaily).where(
                MetricDaily.grain == "campaign", MetricDaily.date == MATURE_DAY,
                MetricDaily.clicks < 20,
            )
        ).first()
        assert low is not None and (low.clicks or 0) == 8
        low_fact_id = low.id
    run_id = _make_context(stage2_env)
    # 非观察类 bid 建议引用低点击行 → VR-04 拒
    verdicts = _validate(run_id, [_base_proposal(fact_evidence_ids=[low_fact_id])])
    assert verdicts[0].verdict == "rejected_hard_rule"
    assert any(v["code"] == "VR-04" for v in verdicts[0].violations)
    # 观察类建议同证据 → 放行
    verdicts2 = _validate(run_id, [
        _base_proposal(fact_evidence_ids=[low_fact_id], action="continue_observation",
                       adjustment="0%")
    ])
    assert verdicts2[0].verdict == "passed", verdicts2[0].violations


def test_validator_vr06_sku_mismatch(stage2_env):
    run_id = _make_context(stage2_env)
    verdicts = _validate(run_id, [_base_proposal(sku="9999")])
    assert any(v["code"] == "VR-06" for v in verdicts[0].violations)


def test_validator_schema_missing_fields(stage2_env):
    run_id = _make_context(stage2_env)
    p = _base_proposal()
    del p["risks"]
    del p["acceptance_criteria"]
    verdicts = _validate(run_id, [p])
    assert verdicts[0].verdict == "rejected_schema"
    detail = str(verdicts[0].violations)
    assert "risks" in detail and "acceptance_criteria" in detail


# ================================================================ T5


def test_t5_1_evidence_traceability(stage2_env):
    run_id = _make_context(stage2_env)
    # 伪造 id 混入真实证据列表：硬规则关（点击合计 21≥20）通过，第三关 EV-01 抓获
    verdicts = _validate(run_id, [
        _base_proposal(),
        _base_proposal(fact_evidence_ids=[_mature_fact_id(), "fake-fact-id-000"]),
    ])
    assert verdicts[0].verdict == "passed"
    assert verdicts[1].verdict == "rejected_missing_evidence"
    assert any(v["code"] == "EV-01" for v in verdicts[1].violations)


def test_t5_2_unknown_rule_code_rejected(stage2_env):
    run_id = _make_context(stage2_env)
    verdicts = _validate(run_id, [_base_proposal(reasoning="依据 HR-99 未知规则")])
    assert verdicts[0].verdict == "rejected_missing_evidence"
    assert any(v["code"] == "EV-02" for v in verdicts[0].violations)


def test_t5_3_run_traceability(stage2_env):
    """分析运行可追溯方法论版本与档案版本（T5）。"""
    run_id = _make_context(stage2_env)
    with session_scope() as s:
        run = s.get(AnalysisRun, run_id)
        assert run.methodology_version_id
        assert run.profile_version_id
        assert json.loads(run.batch_ids_json) == [stage2_env["batch_id"]]
        ev_count = len(s.scalars(
            select(Evidence).where(Evidence.analysis_run_id == run_id)
        ).all())
        assert ev_count > 0


def test_context_requires_profile(env):
    """无档案版本 → 拒绝组装（关键数据缺失必须暂停，PRD 5.1）。"""
    with session_scope() as s:
        upsert_product(s, sku="6999", actor="test")
        s.commit()
    with pytest.raises(ContextError, match="无产品档案"):
        with session_scope() as s:
            assemble_analysis_context(
                s, sku="6999", date_from=DATE_FROM, date_to=DATE_TO,
            )
