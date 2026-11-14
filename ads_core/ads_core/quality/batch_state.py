# -*- coding: utf-8 -*-
"""批次状态机（设计 §3.1）。

draft → identified → quality_running → {complete | partial | incomplete |
needs_confirm | failed}

规则：error → failed；仅 warn → needs_confirm（人工 confirm 后归 complete/partial）；
incomplete/needs_confirm/failed 批次不进入 canonical 与 facts。
"""

from __future__ import annotations

from .checks import Finding

VALID_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"identified"},
    "identified": {"quality_running"},
    "quality_running": {"complete", "partial", "incomplete", "needs_confirm", "failed"},
    "needs_confirm": {"complete", "partial"},
    # 终态：complete/partial/incomplete/failed（重跑质检允许回 quality_running）
    "complete": {"quality_running"},
    "partial": {"quality_running"},
    "incomplete": {"quality_running"},
    "failed": {"quality_running"},
}

BLOCKED_FROM_CANONICAL = {"incomplete", "needs_confirm", "failed"}


def decide_status(findings: list[Finding]) -> str:
    """由质检结果决定批次终态。"""
    if any(f.severity == "error" for f in findings):
        return "failed"
    if any(f.severity == "warn" for f in findings):
        return "needs_confirm"
    return "complete"


def can_transition(current: str, target: str) -> bool:
    return target in VALID_TRANSITIONS.get(current, set())


class InvalidTransition(Exception):
    def __init__(self, current: str, target: str):
        super().__init__(f"非法状态迁移：{current} → {target}")
        self.current = current
        self.target = target


def transition(current: str, target: str) -> str:
    if not can_transition(current, target):
        raise InvalidTransition(current, target)
    return target
