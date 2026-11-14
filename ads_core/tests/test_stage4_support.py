# -*- coding: utf-8 -*-
"""阶段四支撑设施测试：评估统计 + 验收→经验草稿。"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from ads_core.analysis import assemble_analysis_context, validate_proposals
from ads_core.decision import (
    approve_proposal,
    complete_observation,
    create_action,
    create_observation,
    record_evaluation,
    record_receipt,
    submit_proposals,
)
from ads_core.decision.stats import evaluation_stats, evaluation_to_experience_draft
from ads_core.experience import search_experience
from ads_core.storage import Experience, Proposal, session_scope

from .test_stage3_decision_loop import (
    CAMPAIGN,  # noqa: F401
    DATE_FROM,  # noqa: F401
    DATE_TO,  # noqa: F401
    MATURE_DAY,
    TODAY,  # noqa: F401
    _attribution,
    _proposal,  # noqa: F401
)
# approved_proposal fixture 由 conftest 提供


def _fact_id() -> str:
    from ads_core.storage import MetricDaily

    with session_scope() as s:
        m = s.scalars(select(MetricDaily).where(MetricDaily.grain == "campaign")).first()
        return m.id


def _run_to_evaluation(env, approved_proposal, verdict: str) -> str:  # noqa: F811
    """从已批提案走到验收，返回 evaluation_id。"""
    proposal_id, _ = approved_proposal
    with session_scope() as s:
        action, _ = create_action(s, proposal_id, executor="Gordon")
        s.commit()
        record_receipt(s, action.id, result="success", operator="Gordon",
                       operated_at=MATURE_DAY)
        s.commit()
        create_observation(s, action.id)
        s.commit()
        from ads_core.storage import Observation

        obs = s.scalars(select(Observation).where(Observation.action_id == action.id)).first()
        complete_observation(s, obs.id)
        s.commit()
        ev = record_evaluation(
            s, action.id, verdict=verdict,
            attribution=_attribution(other_factors=["测试因素"] if verdict != "valid" else None),
            decided_by="Gordon",
        )
        s.commit()
        return ev.id


def test_evaluation_stats_valid(env, approved_proposal):  # noqa: F811
    _run_to_evaluation(env, approved_proposal, "valid")
    with session_scope() as s:
        st = evaluation_stats(s)
    assert st.total == 1
    assert st.by_verdict == {"valid": 1}
    assert st.effective_rate == 1.0
    assert st.insufficient_ratio == 0.0
    assert st.by_sku.get("6978") == 1


def test_evaluation_stats_insufficient_not_counted(env, approved_proposal):  # noqa: F811
    """insufficient_evidence 不计入对错分母（有效率 None），占比 1.0。"""
    _run_to_evaluation(env, approved_proposal, "insufficient_evidence")
    with session_scope() as s:
        st = evaluation_stats(s)
    assert st.total == 1
    assert st.by_verdict == {"insufficient_evidence": 1}
    assert st.effective_rate is None  # 分母 = valid+invalid+negative = 0
    assert st.insufficient_ratio == 1.0


def test_experience_draft_from_evaluation(env, approved_proposal):  # noqa: F811
    ev_id = _run_to_evaluation(env, approved_proposal, "valid")
    with session_scope() as s:
        exp = evaluation_to_experience_draft(s, ev_id, created_by="Gordon")
        s.commit()
        code = exp.exp_code
    assert exp.status == "pending_verification"
    assert "6978" in exp.scenario

    # 幂等：同 evaluation 再生成返回同一条
    with session_scope() as s:
        exp2 = evaluation_to_experience_draft(s, ev_id, created_by="Gordon")
        s.commit()
        assert exp2.exp_code == code

    # 草稿可被中文检索命中
    with session_scope() as s:
        hits = search_experience(s, "6978")
    assert any(h.exp_code == code for h in hits)

    # 审核流转：Gordon 改判 verified（失败案例路径同理由 Gordon 改 failed）
    with session_scope() as s:
        row = s.scalars(select(Experience).where(Experience.exp_code == code)).first()
        row.status = "verified"
        s.commit()
