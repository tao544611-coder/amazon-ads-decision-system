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

# P1-01：指标引用白名单（建议须显式声明 metrics_referenced，禁止从自然语言推断）。
# 转化侧 = 受归因回填影响的指标（HR-01：未成熟行的转化侧指标不得作为正式结论依据）；
# 体量侧 = 花费/点击及其派生，不受归因回填影响，未成熟行可用于结构初判。
VOLUME_METRICS = {"impressions", "clicks", "spend", "cpc", "ctr"}
CONVERSION_METRICS = {"orders", "sales", "conversions", "units", "acos", "cvr", "roas", "cpa"}
METRIC_ALIASES = {
    "order": "orders", "conversion": "conversions", "conv": "conversions",
    "sale": "sales", "impression": "impressions", "click": "clicks",
    "unit": "units", "cost": "spend", "spends": "spend",
}


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


def _is_number(v: Any) -> bool:
    """数值判定（bool 是 int 子类，须排除——True 不应被当作出价 1）。"""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _adjustment_pct(proposal: dict) -> float | None:
    """调价幅度（百分比数值）：优先独立字段 adjustment_pct（P1-01 拆分），次选
    数值型 adjustment；字符串仅识别百分比形式（`10%` → 10）。纯数字字符串按
    幅度解释（与历史行为兼容），但绝不作为目标出价使用。"""
    pct = proposal.get("adjustment_pct")
    if _is_number(pct):
        return float(pct)
    adj = proposal.get("adjustment")
    if _is_number(adj):
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
    """目标出价：仅接受显式数值字段 target_bid（别名 new_bid 兼容历史建议）。

    P1-01：禁止从 adjustment 等自然语言/字符串中提取数字来决定目标出价
    （旧实现会把 `10%` 调价幅度误识别为目标出价 £10）。未提供时返回 None，
    VR-03 无法核验即不判定，绝不猜数。
    """
    for key in ("target_bid", "new_bid"):
        v = proposal.get(key)
        if _is_number(v):
            return float(v)
    return None


def _referenced_metrics(proposal: dict) -> tuple[list[str] | None, str | None]:
    """读取并归一化建议声明的指标引用（P1-01）。

    返回 (指标列表, 错误说明)。指标列表为 None 表示「无法核验的引用声明」，
    调用方须按安全原则处理（视为引用了转化侧指标，不得绕过成熟度校验）：
    - 字段缺失 / 不是非空字符串列表 / 含未知指标名 → (None, 原因)。
    归一化：小写去空白 + 常见单复数/别名映射；重复去重，保持首次出现顺序。
    """
    raw = proposal.get("metrics_referenced")
    if raw is None:
        return None, "建议未提供 metrics_referenced（指标引用声明）"
    if not isinstance(raw, list) or not raw:
        return None, "metrics_referenced 为空或不是非空数组（引用声明不可核验）"
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            return None, "metrics_referenced 含非字符串/空元素（引用声明不可核验）"
        token = METRIC_ALIASES.get(item.strip().lower(), item.strip().lower())
        if token not in (VOLUME_METRICS | CONVERSION_METRICS):
            return None, f"metrics_referenced 含未知指标名 {item.strip()!r}（引用声明不可核验）"
        if token not in out:
            out.append(token)
    return out, None


def _exception_text(proposal: dict) -> tuple[str, str]:
    """HR-08/HR-09 例外说明字段（P1-02 对齐 YAML：护栏允许例外，须写明理由；
    ±15% 步长例外还须写明回滚方案）。返回 (理由, 回滚方案)，均去除首尾空白。"""
    justification = proposal.get("exception_justification")
    rollback_plan = proposal.get("exception_rollback_plan")
    return (
        justification.strip() if isinstance(justification, str) else "",
        rollback_plan.strip() if isinstance(rollback_plan, str) else "",
    )


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

    # VR-01：未成熟行的转化侧指标作为依据（HR-01/02）——按建议显式声明的
    # metrics_referenced 判断（P1-01），不再从自然语言字段是否为空推断。
    for fid in proposal.get("fact_evidence_ids", []):
        ev = evidence_index.get(fid)
        if ev and ev["evidence_type"] == "metric_daily" and ev["snapshot"].get("maturity") != "mature":
            metrics, ref_error = _referenced_metrics(proposal)
            conversion_used = (
                bool(set(metrics or []) & CONVERSION_METRICS) if metrics is not None else True
            )
            if conversion_used:
                reason = (
                    ref_error or
                    f"声明引用了转化侧指标 {sorted(set(metrics) & CONVERSION_METRICS)}"
                )
                violations.append({
                    "code": "VR-01",
                    "detail": f"证据 {fid[:8]}… 为未成熟行（{ev['snapshot'].get('maturity')}），"
                              f"其转化侧指标不得作为正式结论依据（HR-01/02）；判定依据：{reason}。"
                              "仅声明花费/点击等体量侧指标（metrics_referenced）可用于结构初判",
                })
                break

    if _is_bid_action(proposal):
        justification, rollback_plan = _exception_text(proposal)
        # VR-02：幅度边界（HR-08：更大步长须写明理由与回滚——P1-02 对齐 YAML，
        # 例外 = exception_justification + exception_rollback_plan 同时非空）
        pct = _adjustment_pct(proposal)
        max_pct = float(bid_params.get("max_single_change_pct", 15))
        if pct is not None and abs(pct) > max_pct:
            if justification and rollback_plan:
                pass  # HR-08 例外成立：理由与回滚已随提案进入待批，由人工裁决
            else:
                violations.append({
                    "code": "VR-02",
                    "detail": f"调整幅度 {pct}% 超出方法论上限 ±{max_pct}%（bid_adjustment）。"
                              "HR-08 允许更大步长，但须在提案中写明理由与回滚方案："
                              "exception_justification + exception_rollback_plan 均非空",
                })
        # VR-03：出价上下沿（HR-09：越界须写明理由——例外 = exception_justification 非空）
        bid = _new_bid_value(proposal)
        floor = float(bid_params.get("uk_floor_gbp", 0.10))
        ceiling = float(bid_params.get("uk_ceiling_gbp", 2.00))
        if bid is not None and not (floor <= bid <= ceiling):
            if justification:
                pass  # HR-09 例外成立：理由已随提案进入待批
            else:
                violations.append({
                    "code": "VR-03",
                    "detail": f"目标出价 £{bid} 超出 UK 上下沿 [{floor}, {ceiling}]（bid_adjustment）。"
                              "HR-09 允许越界，但须在提案中写明理由：exception_justification 非空",
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
