# -*- coding: utf-8 -*-
"""建议校验器（D4 三关：Schema → 硬规则 → 证据存在性）。

任一关失败 → rejected_*，不进入审批（阶段三）。判断质量本身不在此保证
（归 AI + 阶段四人工核对，架构评审 D4 自洽说明）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..methodology.params import get_param_group
from ..storage.audit import write_audit
from ..storage.models import AnalysisRun, Evidence, MethodologyRule, now_iso

PROPOSAL_REQUIRED_FIELDS = [
    "sku", "target", "current_performance", "problem", "fact_evidence_ids",
    "event_evidence_ids", "reasoning", "action", "adjustment", "expected_effect",
    "risks", "observation_window", "acceptance_criteria", "needs_approval",
]

OBSERVE_ACTIONS = {"observe", "continue_observation", "观察", "继续观察", "maintain", "维持"}
BID_ACTIONS = {"adjust_bid", "bid", "调价", "降价", "提价", "lower_bid", "raise_bid"}


@dataclass
class ProposalVerdict:
    index: int
    verdict: str                     # passed | rejected_schema | rejected_hard_rule | rejected_missing_evidence
    violations: list[dict] = field(default_factory=dict)  # type: ignore[assignment]
    proposal: dict | None = None


class ValidatorError(Exception):
    pass


def _is_bid_action(proposal: dict) -> bool:
    action = str(proposal.get("action", "")).lower()
    return any(k in action for k in BID_ACTIONS) or action in BID_ACTIONS


def _adjustment_pct(proposal: dict) -> float | None:
    adj = proposal.get("adjustment")
    if isinstance(adj, (int, float)):
        return float(adj)
    if isinstance(adj, str):
        m = re.search(r"(-?\d+(?:\.\d+)?)\s*%", adj)
        if m:
            return float(m.group(1))
        try:
            return float(adj)
        except ValueError:
            return None
    return None


def _new_bid_value(proposal: dict) -> float | None:
    """从建议中提取目标出价（adjustment 中的 £ 数值或 new_bid 字段）。"""
    for key in ("new_bid", "target_bid"):
        v = proposal.get(key)
        if isinstance(v, (int, float)):
            return float(v)
    adj = proposal.get("adjustment")
    if isinstance(adj, str):
        m = re.search(r"(?:£|gbp)?\s*(\d+(?:\.\d+)?)", adj.lower())
        if m:
            return float(m.group(1))
    return None


def _schema_check(proposal: dict) -> list[str]:
    missing = [f for f in PROPOSAL_REQUIRED_FIELDS if f not in proposal or proposal[f] in (None, "")]
    if isinstance(proposal.get("fact_evidence_ids"), list) and not proposal["fact_evidence_ids"]:
        missing.append("fact_evidence_ids (empty)")
    return missing


def _hard_rules(
    session: Session, run: AnalysisRun, proposal: dict,
    evidence_index: dict[str, dict], flags: dict,
) -> list[dict]:
    violations: list[dict] = []
    bid_params = get_param_group(session, "bid_adjustment", run.methodology_version_id)

    # VR-01：未成熟行的转化侧指标作为依据（HR-01/02）
    for fid in proposal.get("fact_evidence_ids", []):
        ev = evidence_index.get(fid)
        if ev and ev["evidence_type"] == "metric_daily" and ev["snapshot"].get("maturity") != "mature":
            conversions_used = any(
                str(proposal.get(k, "")).strip()
                for k in ("current_performance", "reasoning", "expected_effect", "problem")
            )
            if conversions_used:
                violations.append({
                    "code": "VR-01",
                    "detail": f"证据 {fid[:8]}… 为未成熟行（{ev['snapshot'].get('maturity')}），"
                              "其转化侧指标不得作为正式结论依据（HR-01/02）",
                })
                break

    if _is_bid_action(proposal):
        # VR-02：幅度边界
        pct = _adjustment_pct(proposal)
        max_pct = float(bid_params.get("max_single_change_pct", 15))
        if pct is not None and abs(pct) > max_pct:
            violations.append({
                "code": "VR-02",
                "detail": f"调整幅度 {pct}% 超出方法论上限 ±{max_pct}%（bid_adjustment）",
            })
        # VR-03：出价上下沿
        bid = _new_bid_value(proposal)
        floor = float(bid_params.get("uk_floor_gbp", 0.10))
        ceiling = float(bid_params.get("uk_ceiling_gbp", 2.00))
        if bid is not None and not (floor <= bid <= ceiling):
            violations.append({
                "code": "VR-03",
                "detail": f"目标出价 £{bid} 超出 UK 上下沿 [{floor}, {ceiling}]（bid_adjustment）",
            })
        # VR-04：样本量门槛（观察类建议除外）
        min_clicks = int(bid_params.get("min_clicks_for_bid_action", 20))
        action = str(proposal.get("action", "")).lower()
        if not any(k in action for k in OBSERVE_ACTIONS):
            total_clicks = 0
            for fid in proposal.get("fact_evidence_ids", []):
                ev = evidence_index.get(fid, {})
                if ev.get("evidence_type") == "metric_daily":
                    total_clicks += ev.get("snapshot", {}).get("clicks") or 0
            if total_clicks < min_clicks:
                violations.append({
                    "code": "VR-04",
                    "detail": f"证据合计点击 {total_clicks} < {min_clicks}（min_clicks_for_bid_action），"
                              "非观察类 bid 建议不通过（方法论：点击<20 → 继续观察）",
                })

    # VR-05：活跃干扰事件未纳入考量（T4）
    confounding = flags.get("confounding_events") or []
    if confounding and not proposal.get("event_evidence_ids"):
        violations.append({
            "code": "VR-05",
            "detail": f"窗口内存在 {len(confounding)} 个活跃业务事件，但建议未引用任何事件"
                      "（event_evidence_ids 为空——必须说明业务事件对判断的影响）",
        })

    # VR-06：主体一致性
    if proposal.get("sku") != run.sku:
        violations.append({"code": "VR-06", "detail": f"建议 sku {proposal.get('sku')!r} ≠ 分析运行 {run.sku}"})

    return violations


def _evidence_check(
    session: Session, run: AnalysisRun, proposal: dict,
    evidence_index: dict[str, dict],
) -> list[dict]:
    problems: list[dict] = []
    for fid in proposal.get("fact_evidence_ids", []) + proposal.get("event_evidence_ids", []):
        if fid not in evidence_index:
            problems.append({"code": "EV-01", "detail": f"证据 id 不存在或不属于本次分析：{fid}"})
    # reasoning 中的 rule_code 引用（如 HR-01、§3.2）须在方法论库
    rule_codes = set(re.findall(r"\b(HR-\d{2}|K-\d{2})\b", str(proposal.get("reasoning", ""))))
    if rule_codes:
        known = set(session.scalars(
            select(MethodologyRule.rule_code).where(
                MethodologyRule.methodology_version_id == run.methodology_version_id
            )
        ).all())
        unknown = rule_codes - known
        if unknown:
            problems.append({"code": "EV-02", "detail": f"reasoning 引用了未知规则码：{sorted(unknown)}"})
    return problems


def validate_proposals(
    session: Session,
    run_id: str,
    proposals: list[dict],
    actor: str = "cli",
) -> list[ProposalVerdict]:
    run = session.get(AnalysisRun, run_id)
    if run is None:
        raise ValidatorError(f"分析运行不存在：{run_id}")
    if run.status not in ("context_ready", "validating", "proposals_validated", "validated_none"):
        raise ValidatorError(f"运行状态 {run.status} 不允许提交建议")

    run.status = "validating"
    evidence_index: dict[str, dict] = {}
    for ev in session.scalars(
        select(Evidence).where(Evidence.analysis_run_id == run_id)
    ).all():
        evidence_index[ev.ref_id] = {
            "evidence_type": ev.evidence_type,
            "snapshot": json.loads(ev.snapshot_json or "{}"),
        }
    flags = json.loads(run.context_flags_json or "{}")

    verdicts: list[ProposalVerdict] = []
    for i, p in enumerate(proposals):
        if not isinstance(p, dict):
            verdicts.append(ProposalVerdict(i, "rejected_schema",
                                            [{"detail": "建议必须是 JSON 对象"}], None))
            continue
        missing = _schema_check(p)
        if missing:
            verdicts.append(ProposalVerdict(
                i, "rejected_schema",
                [{"detail": f"缺少必填字段：{missing}"}], p,
            ))
            continue
        hard_violations = _hard_rules(session, run, p, evidence_index, flags)
        if hard_violations:
            verdicts.append(ProposalVerdict(i, "rejected_hard_rule", hard_violations, p))
            continue
        ev_problems = _evidence_check(session, run, p, evidence_index)
        if ev_problems:
            verdicts.append(ProposalVerdict(i, "rejected_missing_evidence", ev_problems, p))
            continue
        verdicts.append(ProposalVerdict(i, "passed", [], p))

    passed = sum(1 for v in verdicts if v.verdict == "passed")
    run.status = "proposals_validated" if passed else "validated_none"
    run.proposals_json = json.dumps(
        [
            {"index": v.index, "verdict": v.verdict, "violations": v.violations,
             "proposal": v.proposal}
            for v in verdicts
        ],
        ensure_ascii=False,
    )
    run.completed_at = now_iso()
    write_audit(session, actor, "analysis.validate", "analysis_run", run_id, {
        "total": len(proposals), "passed": passed,
        "rejected": len(proposals) - passed,
    })
    return verdicts
