# -*- coding: utf-8 -*-
"""决策链生命周期：提案 → 审批 → 动作 → 回执 → 观察 → 验收（PRD 4.8–4.10）。

守卫 G1–G6 见 stage3-detailed-design §2；全部程序强制。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..events import events_in_window
from ..methodology.params import get_param_group, maturity_days
from ..storage.audit import write_audit
from ..storage.models import (
    Action,
    AnalysisRun,
    Approval,
    Evaluation,
    ExecutionReceipt,
    MetricDaily,
    Observation,
    Product,
    Proposal,
    now_iso,
)


class DecisionError(Exception):
    pass


# P0-02：真实执行过、对平台产生过影响的动作状态（rolled_back 曾执行后记录层回滚，
# 观察期内其执行效果仍然存在过，故计入同期干扰；pending/executing/execution_failed/
# cancelled 未对平台生效，不计入）。
EXECUTED_ACTION_STATUSES = ("executed", "rolled_back")


# ---------------------------------------------------------------- 提案


@dataclass
class SubmitResult:
    run_id: str
    created: list[str] = field(default_factory=list)
    skipped_existing: int = 0
    skipped_verdict: int = 0


def submit_proposals(session: Session, run_id: str, actor: str = "cli") -> SubmitResult:
    """通过校验的建议（verdict=passed）迁入提案表。"""
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise DecisionError(f"分析运行不存在：{run_id}")
    if not run.proposals_json:
        raise DecisionError(f"运行 {run_id} 无建议记录（先 ads analysis validate）")

    result = SubmitResult(run_id=run_id)
    existing = {
        p.proposal_index
        for p in session.scalars(
            select(Proposal).where(Proposal.analysis_run_id == run_id)
        ).all()
    }
    for idx, entry in enumerate(json.loads(run.proposals_json)):
        if entry.get("verdict") != "passed":
            result.skipped_verdict += 1
            continue
        if idx in existing:
            result.skipped_existing += 1
            continue
        p = entry["proposal"]
        proposal = Proposal(
            analysis_run_id=run_id,
            proposal_index=idx,
            sku=p["sku"],
            target=p["target"],
            action_type=str(p.get("action", "")),
            current_value_json=json.dumps(p.get("current_performance"), ensure_ascii=False),
            proposed_value_json=json.dumps(
                {"adjustment": p.get("adjustment"), "expected_effect": p.get("expected_effect")},
                ensure_ascii=False,
            ),
            adjustment=str(p.get("adjustment", "")),
            payload_json=json.dumps(p, ensure_ascii=False),
            methodology_version_id=run.methodology_version_id,
            profile_version_id=run.profile_version_id,
            status="pending_approval",
            created_by=actor,
            created_at=now_iso(),
        )
        session.add(proposal)
        session.flush()
        result.created.append(proposal.id)

    write_audit(session, actor, "proposal.submit", "analysis_run", run_id,
                {"created": len(result.created), "skipped": result.skipped_verdict})
    return result


# ---------------------------------------------------------------- 审批（T6）


APPROVAL_TRANSITIONS = {
    "approve": "approved",
    "modify_approve": "approved_modified",
    "reject": "rejected",
    "defer": "deferred",
}


def approve_proposal(
    session: Session,
    proposal_id: str,
    decision: str,
    decided_by: str,
    modified_payload: dict | None = None,
    comment: str | None = None,
    actor: str = "cli",
    identity_verified: bool | None = None,
    channel: str | None = None,
) -> Approval:
    """人工审批（T6）。

    身份语义（P1-03）：`decided_by` 是调用者自报姓名，系统无身份认证能力，
    不将其视为已验证身份；审批行通过 `approval_channel` + `identity_verified`
    如实记录审批来源与验证状态——
    - channel="webgate"：本机审批门（仅监听 127.0.0.1 的人工操作页），视为已验证人工审批；
    - channel="mcp"/"cli"：未验证（decided_by 仅为自报留痕，不作为人工审批凭证）。
    """
    if decision not in APPROVAL_TRANSITIONS:
        raise DecisionError(f"decision 须为 {'|'.join(APPROVAL_TRANSITIONS)}")
    proposal = session.get(Proposal, proposal_id)
    if proposal is None:
        raise DecisionError(f"提案不存在：{proposal_id}")
    if proposal.status != "pending_approval":
        raise DecisionError(f"提案状态 {proposal.status} 非 pending_approval（deferred 可重新提请）")

    if channel is None:
        channel = "mcp" if actor.startswith("mcp") else actor
    if identity_verified is None:
        identity_verified = channel == "webgate"  # 仅本机审批门=已验证人工通道

    final = (
        json.dumps(modified_payload, ensure_ascii=False)
        if modified_payload is not None
        else proposal.payload_json
    )
    if decision == "modify_approve" and modified_payload is None:
        raise DecisionError("modify_approve 必须提供修改稿（运营终稿必须保存，PRD 4.8）")

    self_approval = 1 if decided_by == proposal.created_by else 0  # O3-1：单人运营留痕
    approval = Approval(
        proposal_id=proposal_id,
        decision=decision,
        decided_by=decided_by,
        decided_at=now_iso(),
        final_payload_json=final,
        comment=comment,
        self_approval=self_approval,
        approval_channel=channel,
        identity_verified=1 if identity_verified else 0,
        created_at=now_iso(),
    )
    session.add(approval)
    session.flush()
    proposal.status = APPROVAL_TRANSITIONS[decision]

    write_audit(session, actor, "proposal.approve", "proposal", proposal_id,
                {"decision": decision, "decided_by": decided_by,
                 "self_approval": bool(self_approval),
                 "channel": channel, "identity_verified": bool(identity_verified)})
    return approval


def resubmit_proposal(session: Session, proposal_id: str, actor: str = "cli") -> None:
    proposal = session.get(Proposal, proposal_id)
    if proposal is None or proposal.status != "deferred":
        raise DecisionError("仅 deferred 提案可重新提请")
    proposal.status = "pending_approval"
    write_audit(session, actor, "proposal.resubmit", "proposal", proposal_id, {})


# ---------------------------------------------------------------- 动作（T6/T8）


def create_action(
    session: Session,
    proposal_id: str,
    executor: str,
    execute_mode: str = "simulated",
    rollback_policy: dict | None = None,
    approval_id: str | None = None,
    actor: str = "cli",
) -> tuple[Action, bool]:
    """G1/G2/G3：须已审批、引用真实 approval、幂等键防重复（返回 (action, created)）。"""
    if execute_mode not in ("simulated", "manual"):
        raise DecisionError("execute_mode 须为 simulated|manual")
    proposal = session.get(Proposal, proposal_id)
    if proposal is None:
        raise DecisionError(f"提案不存在：{proposal_id}")
    if proposal.status not in ("approved", "approved_modified"):
        raise DecisionError(
            f"提案未获批（status={proposal.status}）——审批与执行分离，无审批不可建动作（T6）"
        )

    approval = None
    if approval_id:
        approval = session.get(Approval, approval_id)
        if approval is None or approval.proposal_id != proposal_id:
            raise DecisionError("approval 不存在或不属于该提案")
    else:
        approval = session.scalars(
            select(Approval).where(Approval.proposal_id == proposal_id)
            .order_by(Approval.created_at.desc())
        ).first()
        if approval is None:
            raise DecisionError("提案无审批记录（T6）")

    idem = f"{proposal_id}:{approval.id}"
    existing = session.scalars(
        select(Action).where(Action.idempotency_key == idem)
    ).first()
    if existing is not None:
        return existing, False  # G3 幂等：Agent 中断重跑安全

    final_payload = json.loads(approval.final_payload_json)
    action = Action(
        proposal_id=proposal_id,
        approval_id=approval.id,
        action_type=proposal.action_type,
        target_json=json.dumps({"target": proposal.target}, ensure_ascii=False),
        original_value_json=proposal.current_value_json,
        final_value_json=json.dumps(
            {"adjustment": final_payload.get("adjustment")}, ensure_ascii=False
        ),
        execute_mode=execute_mode,
        status="pending",
        approved_by=approval.decided_by,
        executor=executor,
        verify_status="unverified",
        rollback_policy_json=json.dumps(rollback_policy, ensure_ascii=False) if rollback_policy else None,
        idempotency_key=idem,
        created_at=now_iso(),
    )
    session.add(action)
    session.flush()
    write_audit(session, actor, "action.create", "action", action.id,
                {"mode": execute_mode, "executor": executor, "idempotency_key": idem})
    return action, True


def record_receipt(
    session: Session,
    action_id: str,
    result: str,
    operator: str,
    operated_at: str | None = None,
    before_state: dict | None = None,
    after_state: dict | None = None,
    raw: dict | None = None,
    actor: str = "cli",
) -> Action:
    """回执核验（G4）：result=unknown → state_unknown，提示先查后台、不盲目重执行。"""
    if result not in ("success", "failed", "unknown"):
        raise DecisionError("result 须为 success|failed|unknown")
    action = session.get(Action, action_id)
    if action is None:
        raise DecisionError(f"动作不存在：{action_id}")
    if action.status in ("cancelled", "rolled_back"):
        raise DecisionError(f"动作已 {action.status}，不接受回执")

    receipt_type = action.execute_mode if action.execute_mode in ("simulated", "manual") else "agent"
    session.add(ExecutionReceipt(
        action_id=action_id,
        receipt_type=receipt_type,
        operator=operator,
        operated_at=operated_at or now_iso(),
        result=result,
        before_state_json=json.dumps(before_state, ensure_ascii=False) if before_state else None,
        after_state_json=json.dumps(after_state, ensure_ascii=False) if after_state else None,
        raw_json=json.dumps(raw, ensure_ascii=False) if raw else None,
        received_at=now_iso(),
        created_at=now_iso(),
    ))

    if result == "success":
        action.status = "executed"
        action.executed_at = operated_at or now_iso()
        action.verify_status = "verified" if action.execute_mode == "simulated" else "unverified"
    elif result == "failed":
        action.status = "execution_failed"
    else:  # unknown（G4/T8）
        action.status = "pending"
        action.verify_status = "state_unknown"
    write_audit(session, actor, "action.receipt", "action", action_id, {"result": result})
    return action


def mark_action_rolled_back(
    session: Session, action_id: str, reason: str, by: str, actor: str = "cli"
) -> Action:
    """记录层回滚（T8 · P1-03 语义澄清）：仅推进本地动作状态并留痕。

    本函数只做三件事：状态改为 rolled_back、写入回滚原因/操作人/时间、审计留痕
    （历史不删不覆盖）。它 **不向亚马逊后台发送任何反向操作**，不等于广告原始
    状态已恢复——外部平台的实际恢复须另行人工操作并经回执核验（未来如需真正的
    外部回滚，作为独立需求开发）。历史命名 rollback_action 保留为兼容别名。
    """
    action = session.get(Action, action_id)
    if action is None:
        raise DecisionError(f"动作不存在：{action_id}")
    if action.status != "executed":
        raise DecisionError(f"仅 executed 动作可回滚（当前 {action.status}）")
    action.status = "rolled_back"
    action.result_json = json.dumps(
        {
            "rollback_reason": reason,
            "rolled_back_by": by,
            "rolled_back_at": now_iso(),
            "rollback_scope": "record_layer_only",
            "external_platform_restored": False,
        },
        ensure_ascii=False,
    )
    write_audit(session, actor, "action.rollback", "action", action_id,
                {"reason": reason, "scope": "record_layer_only"})
    return action


# 兼容别名：旧调用面（CLI/tests/外部脚本）无需改动；语义见 mark_action_rolled_back
rollback_action = mark_action_rolled_back


# ---------------------------------------------------------------- 观察（F5/T7）


def _action_sku_product(session: Session, action: Action) -> tuple[Proposal, Product]:
    """P0-01：动作 → 提案 → SKU → 产品（观察数据 SKU 归属锚点）。

    提案 SKU 缺产品档案或无关联提案时直接报错——归属不明不允许建观察
    （数据无法确认归属不得静默纳入计算，PRD 5.1）。
    多 SKU 变更须拆成多个提案/动作（每动作唯一 SKU），观察窗不做跨 SKU 混合汇总。
    """
    proposal = session.get(Proposal, action.proposal_id)
    if proposal is None:
        raise DecisionError(f"动作 {action.id} 无关联提案，无法确定 SKU 归属")
    product = session.scalars(
        select(Product).where(Product.sku == proposal.sku)
    ).first()
    if product is None:
        raise DecisionError(
            f"提案 SKU {proposal.sku!r} 无产品档案，无法确定观察数据归属（先 ads product add）"
        )
    return proposal, product


def _sku_campaign_rows(
    session: Session, product_id: str, date_from: str, date_to: str,
    end_inclusive: bool,
) -> tuple[list[MetricDaily], int]:
    """P0-01：窗口内该 SKU 的成熟 campaign 事实行（按 product_id 归属过滤）。

    同窗口内 product_id 为 NULL 的 campaign 行无法确认 SKU 归属（活动未映射或
    事实行先于映射建立）——一律不纳入计算，仅计数返回供标注「数据不足」；
    绝不静默混入。基线与观察期必须使用同一过滤口径。
    """
    base = [
        MetricDaily.grain == "campaign",
        MetricDaily.date >= date_from,
        MetricDaily.maturity == "mature",
    ]
    end_cond = MetricDaily.date <= date_to if end_inclusive else MetricDaily.date < date_to
    rows = session.scalars(
        select(MetricDaily).where(*base, end_cond, MetricDaily.product_id == product_id)
    ).all()
    unattributed = session.scalar(
        select(func.count()).select_from(MetricDaily).where(
            *base, end_cond, MetricDaily.product_id.is_(None),
        )
    )
    return list(rows), int(unattributed or 0)


def _sku_attribution_note(unattributed_rows: int) -> dict:
    return {
        "filter": "metric_daily.product_id == 动作提案 SKU 的产品（campaign 粒度）",
        "unattributed_campaign_rows": unattributed_rows,
        "attribution_complete": unattributed_rows == 0,
    }


def _concurrent_action_ids(
    session: Session, exclude_action_id: str, start: date, end: date,
) -> list[str]:
    """P0-02：观察窗 [start, end]（含首尾日）内真实执行过的其他动作。

    仅计入 status ∈ EXECUTED_ACTION_STATUSES 且 executed_at 日期落在窗口内的动作；
    executed_at 日期无法解析的动作不计入（宁缺勿误报）。窗口边界与
    complete_observation 的日期口径一致（start/end 均包含）。
    """
    actions = session.scalars(
        select(Action).where(
            Action.id != exclude_action_id,
            Action.status.in_(EXECUTED_ACTION_STATUSES),
            Action.executed_at.is_not(None),
        )
    ).all()
    ids: list[str] = []
    for a in actions:
        try:
            exec_day = date.fromisoformat(str(a.executed_at)[:10])
        except (TypeError, ValueError):
            continue
        if start <= exec_day <= end:
            ids.append(a.id)
    return sorted(ids)


def create_observation(session: Session, action_id: str, actor: str = "cli") -> Observation:
    """G5：观察窗 = max(验证窗, 归因满窗)（F5）；基准快照 + 同期事件/动作自动拉取（T7）。

    P0-01：基准行按动作提案 SKU 的 product_id 过滤；无法确认归属的行计数标注，
    不纳入计算。P0-02：同期动作仅识别窗口内真实执行过的动作。
    """
    action = session.get(Action, action_id)
    if action is None:
        raise DecisionError(f"动作不存在：{action_id}")
    if action.status != "executed":
        raise DecisionError(f"仅 executed 动作可建观察（当前 {action.status}）")
    existing = session.scalars(
        select(Observation).where(Observation.action_id == action_id)
    ).first()
    if existing is not None:
        return existing

    proposal, product = _action_sku_product(session, action)
    mv = proposal.methodology_version_id if proposal else None
    acceptance = get_param_group(session, "acceptance", mv)
    sp_days = maturity_days(session, "sp", mv)
    sb_days = maturity_days(session, "sb", mv)

    # 验证窗：acceptance.window_default = "7 天或下期周检" → 取 7 天
    verify_days = 7
    attribution_days = max(sp_days, sb_days)  # 从严（HR-02）
    window_days = max(verify_days, attribution_days)  # F5

    start = date.fromisoformat((action.executed_at or now_iso())[:10])
    end = start + timedelta(days=window_days)

    # 基准：动作前该 SKU 窗口事实快照（执行日前 7 天成熟行聚合；SKU 归属过滤 P0-01）
    baseline_rows, baseline_unattributed = _sku_campaign_rows(
        session, product.id, (start - timedelta(days=7)).isoformat(), start.isoformat(),
        end_inclusive=False,
    )
    baseline = {
        "period": "exec-7d~exec",
        "sku": proposal.sku,
        "product_id": product.id,
        "sku_attribution": _sku_attribution_note(baseline_unattributed),
        "rows": [
            {"fact_id": m.id, "date": m.date, "spend": m.spend, "clicks": m.clicks,
             "orders": m.orders, "sales": m.sales, "acos": m.acos}
            for m in baseline_rows
        ],
    }

    # 同期干扰（T7）：观察窗内业务事件 + 窗口内真实执行的其他动作（P0-02）
    events = events_in_window(session, start.isoformat(), end.isoformat())
    others = _concurrent_action_ids(session, action_id, start, end)

    observation = Observation(
        action_id=action_id,
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        baseline_json=json.dumps(baseline, ensure_ascii=False),
        concurrent_events_json=json.dumps(
            [{"event_id": e.id, "event_type": e.event_type, "starts_at": e.starts_at,
              "ends_at": e.ends_at, "description": e.description} for e in events],
            ensure_ascii=False,
        ),
        concurrent_actions_json=json.dumps(others, ensure_ascii=False),
        status="open",
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    session.add(observation)
    session.flush()
    write_audit(session, actor, "observation.create", "observation", observation.id,
                {"window": [observation.window_start, observation.window_end],
                 "concurrent_events": len(events), "concurrent_actions": len(others),
                 "sku": proposal.sku,
                 "baseline_unattributed_rows": baseline_unattributed})
    return observation


def complete_observation(session: Session, observation_id: str, actor: str = "cli") -> Observation:
    """观察窗完结：窗口内事实快照（与基线同一 SKU 归属过滤口径，P0-01）。"""
    obs = session.get(Observation, observation_id)
    if obs is None:
        raise DecisionError(f"观察不存在：{observation_id}")
    if obs.status != "open":
        raise DecisionError(f"观察状态 {obs.status} 非 open")
    action = session.get(Action, obs.action_id)
    proposal, product = _action_sku_product(session, action)

    # 动作后数据：窗口内该 SKU 事实快照（P0-01：与基线同一过滤口径）
    post_rows, post_unattributed = _sku_campaign_rows(
        session, product.id, obs.window_start, obs.window_end, end_inclusive=True,
    )
    obs.post_data_json = json.dumps(
        {"period": [obs.window_start, obs.window_end],
         "sku": proposal.sku,
         "product_id": product.id,
         "sku_attribution": _sku_attribution_note(post_unattributed),
         "rows": [
             {"fact_id": m.id, "date": m.date, "spend": m.spend, "clicks": m.clicks,
              "orders": m.orders, "sales": m.sales, "acos": m.acos}
             for m in post_rows
         ]},
        ensure_ascii=False,
    )
    obs.status = "window_complete"
    obs.updated_at = now_iso()
    write_audit(session, actor, "observation.complete", "observation", observation_id,
                {"post_rows": len(post_rows), "sku": proposal.sku,
                 "post_unattributed_rows": post_unattributed})
    return obs


# ---------------------------------------------------------------- 验收（T7）


ATTRIBUTION_KEYS = ("what_changed", "possibly_related", "strongly_supported", "other_factors")


def record_evaluation(
    session: Session,
    action_id: str,
    verdict: str,
    attribution: dict,
    decided_by: str,
    evidence_ids: list[str] | None = None,
    maturity_note: str | None = None,
    evidence_strength: str = "medium",
    actor: str = "cli",
) -> Evaluation:
    if verdict not in ("valid", "invalid", "negative", "insufficient_evidence"):
        raise DecisionError("verdict 须为 valid|invalid|negative|insufficient_evidence")
    if not attribution or any(k not in attribution for k in ATTRIBUTION_KEYS):
        raise DecisionError(f"attribution 须含四键：{ATTRIBUTION_KEYS}（T7 归因分层）")

    action = session.get(Action, action_id)
    if action is None:
        raise DecisionError(f"动作不存在：{action_id}")
    obs = session.scalars(
        select(Observation).where(Observation.action_id == action_id)
    ).first()
    if obs is None or obs.status != "window_complete":
        raise DecisionError("观察未完结（先 ads observation complete）")

    # G6/T7：存在同期干扰时必须给出 other_factors 或判证据不足
    concurrent_events = json.loads(obs.concurrent_events_json or "[]")
    concurrent_actions = json.loads(obs.concurrent_actions_json or "[]")
    has_interference = bool(concurrent_events or concurrent_actions)
    if has_interference and not attribution.get("other_factors") and verdict != "insufficient_evidence":
        raise DecisionError(
            "观察窗内存在同期事件/动作（T7）：必须填写 other_factors 或将 verdict 判为 "
            "insufficient_evidence，不得强行归因"
        )

    rollback_suggested = 0
    if verdict in ("invalid", "negative") and action.rollback_policy_json:
        policy = json.loads(action.rollback_policy_json)
        if policy.get("rollback_condition"):
            rollback_suggested = 1  # O3-3：只建议，不自动执行

    evaluation = Evaluation(
        action_id=action_id,
        observation_id=obs.id,
        verdict=verdict,
        attribution_json=json.dumps(attribution, ensure_ascii=False),
        evidence_ids_json=json.dumps(evidence_ids or [], ensure_ascii=False),
        maturity_note=maturity_note,
        evidence_strength=evidence_strength,
        rollback_suggested=rollback_suggested,
        decided_by=decided_by,
        decided_at=now_iso(),
        created_at=now_iso(),
    )
    session.add(evaluation)
    obs.status = "evaluated"
    obs.updated_at = now_iso()
    write_audit(session, actor, "evaluation.record", "action", action_id,
                {"verdict": verdict, "rollback_suggested": bool(rollback_suggested)})
    return evaluation
