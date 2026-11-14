# -*- coding: utf-8 -*-
"""decision：提案/审批/动作/回执/观察/验收生命周期（PRD 4.8–4.10）。"""

from .lifecycle import (
    DecisionError,
    SubmitResult,
    approve_proposal,
    complete_observation,
    create_action,
    create_observation,
    mark_action_rolled_back,
    record_evaluation,
    record_receipt,
    resubmit_proposal,
    rollback_action,  # 兼容别名（= mark_action_rolled_back）
    submit_proposals,
)

__all__ = [
    "DecisionError",
    "SubmitResult",
    "approve_proposal",
    "complete_observation",
    "create_action",
    "create_observation",
    "mark_action_rolled_back",
    "record_evaluation",
    "record_receipt",
    "resubmit_proposal",
    "rollback_action",
    "submit_proposals",
]
