# -*- coding: utf-8 -*-
"""评估统计（阶段四：建议准确性视图）与验收→经验草稿通道。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..experience import add_experience
from ..storage.audit import write_audit
from ..storage.models import (
    Action,
    Evaluation,
    Experience,
    Proposal,
    now_iso,
)


@dataclass
class EvaluationStats:
    total: int = 0
    by_verdict: dict = field(default_factory=dict)
    effective_rate: float | None = None       # valid / (valid+invalid+negative)
    insufficient_ratio: float | None = None   # 证据不足占比（不计入对错）
    rollback_suggested: int = 0
    by_action_type: dict = field(default_factory=dict)
    by_sku: dict = field(default_factory=dict)


def evaluation_stats(
    session: Session, date_from: str | None = None, date_to: str | None = None
) -> EvaluationStats:
    stmt = select(Evaluation)
    if date_from:
        stmt = stmt.where(Evaluation.decided_at >= date_from)
    if date_to:
        stmt = stmt.where(Evaluation.decided_at <= date_to)
    rows = session.scalars(stmt).all()

    stats = EvaluationStats(total=len(rows))
    for ev in rows:
        stats.by_verdict[ev.verdict] = stats.by_verdict.get(ev.verdict, 0) + 1
        if ev.rollback_suggested:
            stats.rollback_suggested += 1
        action = session.get(Action, ev.action_id)
        if action is None:
            continue
        stats.by_action_type[action.action_type] = stats.by_action_type.get(action.action_type, 0) + 1
        proposal = session.get(Proposal, action.proposal_id)
        if proposal is not None:
            stats.by_sku[proposal.sku] = stats.by_sku.get(proposal.sku, 0) + 1

    decided = sum(
        stats.by_verdict.get(v, 0) for v in ("valid", "invalid", "negative")
    )
    if decided:
        stats.effective_rate = round(stats.by_verdict.get("valid", 0) / decided, 4)
    if stats.total:
        stats.insufficient_ratio = round(
            stats.by_verdict.get("insufficient_evidence", 0) / stats.total, 4
        )
    return stats


def evaluation_to_experience_draft(
    session: Session,
    evaluation_id: str,
    created_by: str,
    actor: str = "cli",
) -> Experience:
    """验收结果一键转经验草稿（PRD 4.11 经验来源；AI 判断错误案例经人工改 failed）。"""
    ev = session.get(Evaluation, evaluation_id)
    if ev is None:
        raise ValueError(f"验收不存在：{evaluation_id}")
    action = session.get(Action, ev.action_id)
    proposal = session.get(Proposal, action.proposal_id) if action else None
    if proposal is None:
        raise ValueError("动作缺提案，无法生成经验")

    attribution = json.loads(ev.attribution_json)
    existing = session.scalars(
        select(Experience).where(Experience.related_action_id == ev.action_id)
    ).first()
    if existing is not None:
        return existing  # 一动作一经验草稿（幂等）

    proposal_payload = json.loads(proposal.payload_json)
    outcome_map = {
        "valid": "动作有效（已验证）",
        "invalid": "动作无效",
        "negative": "动作产生负面影响",
        "insufficient_evidence": "证据不足未判定",
    }
    exp = add_experience(
        session,
        scenario=f"{proposal.sku} {proposal.action_type}（{proposal.target[:60]}）",
        original_problem=str(proposal_payload.get("problem", "")),
        created_by=created_by,
        ai_judgment=str(proposal_payload.get("reasoning", ""))[:500],
        operator_correction=None,
        actual_action=f"{action.action_type} {json.loads(action.final_value_json or '{}').get('adjustment', '')}",
        observation_result=outcome_map.get(ev.verdict, ev.verdict)
        + (f"；同期因素：{attribution.get('other_factors')}" if attribution.get("other_factors") else ""),
        evidence_strength=ev.evidence_strength,
        status="pending_verification",  # Gordon 审核后改 verified / failed
        related_action_id=ev.action_id,
        actor=actor,
    )
    write_audit(session, actor, "experience.draft", "evaluation", evaluation_id,
                {"exp_code": exp.exp_code})
    return exp
