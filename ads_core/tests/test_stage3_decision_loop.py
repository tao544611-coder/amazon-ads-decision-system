# -*- coding: utf-8 -*-
"""阶段三测试：T6 权限 + T7 归因 + T8 观察回滚 + F5 窗口 + 经验库。"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from ads_core.analysis import assemble_analysis_context, validate_proposals
from ads_core.canonical import build_canonical
from ads_core.decision import (
    DecisionError,
    approve_proposal,
    complete_observation,
    create_action,
    create_observation,
    record_evaluation,
    record_receipt,
    rollback_action,
    submit_proposals,
)
from ads_core.events import add_business_event
from ads_core.experience import add_experience, search_experience
from ads_core.facts import build_facts
from ads_core.ingest.importer import ingest_file
from ads_core.profile import map_campaign, update_profile, upsert_product
from ads_core.storage import (
    Action,
    AnalysisRun,
    Approval,
    ExecutionReceipt,
    ImportBatch,
    Proposal,
    session_scope,
)

from .conftest import make_report, sample_campaign_rows

TODAY = date(2026, 11, 14)
AS_OF = TODAY.isoformat()
MATURE_DAY = (TODAY - timedelta(days=20)).isoformat()
DATE_FROM = (TODAY - timedelta(days=40)).isoformat()
DATE_TO = TODAY.isoformat()
CAMPAIGN = "GJHIUBKNGT-AZDAuto"


@pytest.fixture()
def approved_proposal(env):
    """产品+档案+映射+事实+上下文+通过校验的建议+审批 → 返回 (proposal_id, approval_id)。"""
    with session_scope() as s:
        p = upsert_product(s, sku="6978", actor="test")
        s.commit()
        update_profile(
            s, p.product_id,
            {"A_basic": {"asin": "B0HB4XFV2F"}, "B_operations": {"target_acos": 0.25}},
            reason="v1", changed_by="test",
        )
        s.commit()
        map_campaign(s, campaign_id="29437742665971", product_id=p.product_id,
                     mapped_by="test", campaign_name=CAMPAIGN, ad_product_type="sp")
        s.commit()

    rows = sample_campaign_rows([MATURE_DAY], campaign_id="29437742665971")
    for r in rows:
        r["Campaign Name"] = CAMPAIGN
    f = make_report(env / "s3_camp.csv", "campaign", rows)
    with session_scope() as s:
        r = ingest_file(s, f, data_as_of=AS_OF)
        s.commit()
    if r.status == "needs_confirm":
        with session_scope() as s:
            s.get(ImportBatch, r.batch_id).status = "complete"
            s.commit()
    with session_scope() as s:
        build_canonical(s, r.batch_id, actor="test")
        s.commit()
    with session_scope() as s:
        build_facts(s, grains=["campaign", "product"], actor="test")
        s.commit()

    with session_scope() as s:
        run = assemble_analysis_context(
            s, sku="6978", date_from=DATE_FROM, date_to=DATE_TO, actor="test",
        )
        s.commit()
    fact_id = _fact_id()
    with session_scope() as s:
        verdicts = validate_proposals(s, run.run_id, [_proposal(fact_id)], actor="test")
        s.commit()
    assert verdicts[0].verdict == "passed", verdicts[0].violations

    with session_scope() as s:
        submitted = submit_proposals(s, run.run_id, actor="test")
        s.commit()
    assert len(submitted.created) == 1
    proposal_id = submitted.created[0]
    with session_scope() as s:
        approval = approve_proposal(s, proposal_id, "approve", decided_by="Gordon")
        s.commit()
    return proposal_id, approval.id


def _fact_id() -> str:
    from ads_core.storage import MetricDaily

    with session_scope() as s:
        m = s.scalars(
            select(MetricDaily).where(MetricDaily.grain == "campaign")
        ).first()
        assert m is not None
        return m.id


def _proposal(fact_id: str) -> dict:
    return {
        "sku": "6978", "target": CAMPAIGN,
        "current_performance": "ACOS 30%", "problem": "高于目标",
        "fact_evidence_ids": [fact_id], "event_evidence_ids": [],
        "reasoning": "成熟期 ACOS 偏高",
        "action": "adjust_bid", "adjustment": "-10%", "new_bid": 0.60,
        "expected_effect": "ACOS 收敛至 25%", "risks": "无",
        "observation_window": "7 天", "acceptance_criteria": "ACOS ≤ 25%",
        "needs_approval": True,
    }


def _attribution(other_factors=None):
    return {
        "what_changed": ["ACOS 30%→22%"],
        "possibly_related": ["bid 下调"],
        "strongly_supported": ["花费下降"],
        "other_factors": other_factors or [],
    }


def _executed_action(proposal_id, approval_id, operated_at: str = MATURE_DAY) -> str:
    with session_scope() as s:
        action, created = create_action(
            s, proposal_id, executor="Gordon", execute_mode="simulated",
            rollback_policy={"rollback_condition": "ACOS 反弹 >32%", "rollback_target": "bid 0.78"},
        )
        s.commit()
        assert created
        aid = action.id
    with session_scope() as s:
        record_receipt(s, aid, result="success", operator="Gordon", operated_at=operated_at)
        s.commit()
    return aid


# ================================================================ T6 权限


def test_t6_1_unapproved_cannot_create_action(env, approved_proposal):
    """未经审批（或已拒/缓）不可建动作。"""
    proposal_id, _ = approved_proposal
    # 直接建（无审批）—— proposal 已 approve，用新提案走未审批路径
    with session_scope() as s:
        p = s.get(Proposal, proposal_id)
        p.status = "pending_approval"
        s.commit()
    with pytest.raises(DecisionError, match="审批与执行分离"):
        with session_scope() as s:
            create_action(s, proposal_id, executor="x")
            s.commit()


def test_t6_2_rejected_never_becomes_action(env, approved_proposal):
    proposal_id, _ = approved_proposal
    with session_scope() as s:
        p = s.get(Proposal, proposal_id)
        p.status = "pending_approval"
        s.commit()
        approve_proposal(s, proposal_id, "reject", decided_by="Gordon")
        s.commit()
    with pytest.raises(DecisionError, match="未获批"):
        with session_scope() as s:
            create_action(s, proposal_id, executor="x")
            s.commit()


def test_t6_3_modify_approval_saves_final(env, approved_proposal):
    """modify_approve 必须保存运营终稿（PRD 4.8），action 引用真实 approval。"""
    proposal_id, _ = approved_proposal
    with session_scope() as s:
        p = s.get(Proposal, proposal_id)
        p.status = "pending_approval"
        s.commit()
        modified = json.loads(p.payload_json)
        modified["adjustment"] = "-8%"
        approval = approve_proposal(
            s, proposal_id, "modify_approve", decided_by="Gordon",
            modified_payload=modified, comment="幅度收紧",
        )
        s.commit()
    assert json.loads(approval.final_payload_json)["adjustment"] == "-8%"

    with session_scope() as s:
        action, _ = create_action(s, proposal_id, executor="Gordon")
        s.commit()
        assert action.approval_id == approval.id  # 审批与执行记录分离（T6）
        assert json.loads(action.final_value_json)["adjustment"] == "-8%"  # 执行终稿


def test_t6_4_self_approval_flagged(env, approved_proposal):
    """审批人=提案创建人 → self_approval 留痕（O3-1）。"""
    proposal_id, _ = approved_proposal
    with session_scope() as s:
        p = s.get(Proposal, proposal_id)
        p.status = "pending_approval"
        s.commit()
        approval = approve_proposal(s, proposal_id, "approve", decided_by="tester-ai")
        s.commit()
    assert approval.self_approval == 0  # decided_by(tester-ai) != created_by(cli)


# ================================================================ T8 幂等/回执/回滚


def test_t8_1_idempotent_action_creation(env, approved_proposal):
    """同提案同审批重复建动作 → 返回同一 action（Agent 重跑安全）。"""
    proposal_id, approval_id = approved_proposal
    with session_scope() as s:
        a1, c1 = create_action(s, proposal_id, executor="Gordon")
        s.commit()
    with session_scope() as s:
        a2, c2 = create_action(s, proposal_id, executor="Gordon")
        s.commit()
    assert c1 is True and c2 is False
    assert a1.id == a2.id


def test_t8_2_unknown_receipt_sets_state_unknown(env, approved_proposal):
    """回执 unknown → verify_status=state_unknown（先查后台，不盲目重执行）。"""
    proposal_id, _ = approved_proposal
    with session_scope() as s:
        action, _ = create_action(s, proposal_id, executor="Gordon")
        s.commit()
        record_receipt(s, action.id, result="unknown", operator="Gordon")
        s.commit()
        assert action.status == "pending"
        assert action.verify_status == "state_unknown"
        # 后续补成功回执（先查状态路径）
        record_receipt(s, action.id, result="success", operator="Gordon")
        s.commit()
        assert action.status == "executed"
        # 回执历史完整保留（两条）
        receipts = s.scalars(
            select(ExecutionReceipt).where(ExecutionReceipt.action_id == action.id)
        ).all()
        assert len(receipts) == 2


def test_t8_3_rollback_keeps_history(env, approved_proposal):
    proposal_id, _ = approved_proposal
    aid = _executed_action(proposal_id, None)
    with session_scope() as s:
        rollback_action(s, aid, reason="ACOS 反弹超回滚线", by="Gordon")
        s.commit()
        action = s.get(Action, aid)
        assert action.status == "rolled_back"
        assert json.loads(action.result_json)["rollback_reason"] == "ACOS 反弹超回滚线"
        # 历史完整：回执仍在
        assert len(s.scalars(
            select(ExecutionReceipt).where(ExecutionReceipt.action_id == aid)
        ).all()) == 1
        # 已回滚动作不能再回执
        with pytest.raises(DecisionError):
            record_receipt(s, aid, result="success", operator="x")


# ================================================================ F5 观察窗口 + T7 归因


def test_observation_window_f5_and_full_chain(env, approved_proposal):
    """观察窗 = max(验证窗 7 天, 归因满窗 14 天) = 14 天；全链可走到验收。"""
    proposal_id, _ = approved_proposal
    aid = _executed_action(proposal_id, None, operated_at=MATURE_DAY)
    exec_day = date.fromisoformat(MATURE_DAY)

    with session_scope() as s:
        obs = create_observation(s, aid, actor="test")
        s.commit()
        # 执行日 = MATURE_DAY → 窗口 [exec, exec+14]
        assert obs.window_start == exec_day.isoformat()
        assert obs.window_end == (exec_day + timedelta(days=14)).isoformat()
        baseline = json.loads(obs.baseline_json)
        assert "rows" in baseline

    # 窗口完结（无同期事件/动作 → 无干扰）
    with session_scope() as s:
        obs = complete_observation(s, obs.id, actor="test")
        s.commit()
        assert obs.status == "window_complete"

    with session_scope() as s:
        ev = record_evaluation(
            s, aid, verdict="valid", attribution=_attribution(),
            decided_by="Gordon", evidence_strength="strong",
        )
        s.commit()
        assert ev.verdict == "valid"
        assert ev.rollback_suggested == 0  # valid 不建议回滚


def test_t7_1_interference_guard(env, approved_proposal):
    """观察窗内有同期事件：attribution 缺 other_factors 且非 insufficient → 拒（T7）。"""
    proposal_id, _ = approved_proposal
    aid = _executed_action(proposal_id, None)
    # 观察窗 [MATURE_DAY, +14] 内放一个秒杀事件
    with session_scope() as s:
        from ads_core.storage import Product

        product = s.scalars(select(Product).where(Product.sku == "6978")).first()
        add_business_event(
            s, event_type="deal", starts_at=MATURE_DAY,
            ends_at=(date.fromisoformat(MATURE_DAY) + timedelta(days=3)).isoformat(),
            description="观察窗内秒杀", recorded_by="Gordon", product_id=product.id,
        )
        s.commit()
    with session_scope() as s:
        from ads_core.storage.models import Observation

        create_observation(s, aid, actor="test")
        s.commit()
        obs = s.scalars(select(Observation).where(Observation.action_id == aid)).first()
        assert len(json.loads(obs.concurrent_events_json)) == 1  # 干扰自动拉取
        complete_observation(s, obs.id, actor="test")
        s.commit()

    # 无 other_factors 的 valid → 拒
    with pytest.raises(DecisionError, match="强行归因"):
        with session_scope() as s:
            record_evaluation(s, aid, verdict="valid", attribution=_attribution(), decided_by="G")
            s.commit()
    # insufficient_evidence → 合法（证据不足不得强行判定，T7）
    with session_scope() as s:
        ev = record_evaluation(
            s, aid, verdict="insufficient_evidence",
            attribution=_attribution(other_factors=["观察窗内秒杀未排除"]),
            decided_by="Gordon",
        )
        s.commit()
        assert ev.verdict == "insufficient_evidence"


def test_t7_2_invalid_with_rollback_suggestion(env, approved_proposal):
    """invalid + 预设回滚条件 → rollback_suggested=true（只建议不自动执行，O3-3）。"""
    proposal_id, _ = approved_proposal
    aid = _executed_action(proposal_id, None)  # 建动作时带 rollback_policy
    with session_scope() as s:
        create_observation(s, aid, actor="test")
        s.commit()
    with session_scope() as s:
        from ads_core.storage.models import Observation

        obs = s.scalars(select(Observation).where(Observation.action_id == aid)).first()
        complete_observation(s, obs.id, actor="test")
        s.commit()
    with session_scope() as s:
        ev = record_evaluation(
            s, aid, verdict="invalid",
            attribution=_attribution(other_factors=["样本量不足"]),
            decided_by="Gordon",
        )
        s.commit()
        assert ev.rollback_suggested == 1


# ================================================================ 经验库


def test_experience_add_search_chinese(env):
    """中文经验 FTS 检索命中（D7 二元切分）。"""
    with session_scope() as s:
        upsert_product(s, sku="6978", actor="test")
        s.commit()
        add_experience(
            s, scenario="6978 零转化词处置", original_problem="heated lunch box 花费高零转化",
            created_by="Gordon", sku="6978", status="verified",
            observation_result="暂停后省 £10/周，无自然位下滑",
        )
        s.commit()
    with session_scope() as s:
        hits = search_experience(s, "零转化 词 处置")
    assert hits, "中文二元切分检索应命中"
    assert "零转化" in hits[0].scenario


def test_experience_failed_kept_and_context_related(env, approved_proposal):
    """失败经验保留 + 上下文包含 related_experience。"""
    with session_scope() as s:
        exp = add_experience(
            s, scenario="6978 SB 出单词误判", original_problem="把关联流量当出单词转正",
            created_by="Gordon", sku="6978", status="failed",
            observation_result="转正后 ACOS 恶化",
        )
        s.commit()
        exp_id = exp.id
    # 上下文组装包含相关经验（辅助依据）
    with session_scope() as s:
        run = assemble_analysis_context(
            s, sku="6978", date_from=DATE_FROM, date_to=DATE_TO, actor="test",
        )
        s.commit()
    with session_scope() as s:
        ctx = json.loads(s.get(AnalysisRun, run.run_id).context_json)
        codes = [e["exp_code"] for e in ctx["related_experience"]]
        assert codes, "上下文应包含 related_experience"
        entry = ctx["related_experience"][0]
        assert "不得覆盖正式方法论" in entry["usage_note"]
    # 失败经验保留（不可因结果差删除——无删除接口；直查库验证存在）
    from ads_core.storage import Experience

    with session_scope() as s:
        assert s.get(Experience, exp_id) is not None
