# -*- coding: utf-8 -*-
"""analysis：上下文组装 + 建议校验器（D3/D4）。"""

from .context import ContextError, ContextResult, assemble_analysis_context
from .validator import (
    PROPOSAL_REQUIRED_FIELDS,
    ProposalVerdict,
    ValidatorError,
    validate_proposals,
)

__all__ = [
    "ContextError",
    "ContextResult",
    "assemble_analysis_context",
    "PROPOSAL_REQUIRED_FIELDS",
    "ProposalVerdict",
    "ValidatorError",
    "validate_proposals",
]
