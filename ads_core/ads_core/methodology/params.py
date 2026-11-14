# -*- coding: utf-8 -*-
"""运行时方法论参数读取（D10：参数取自方法论，不硬编码业务数值）。"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..storage.models import MethodologyRule, MethodologyVersion
from .loader import latest_released

# 设计级兜底仅用于无方法论时的显式报错提示，不用作静默默认值。


class MethodologyNotImported(RuntimeError):
    pass


def require_released_version(session: Session) -> MethodologyVersion:
    version = latest_released(session)
    if version is None:
        raise MethodologyNotImported(
            "库中无已发布方法论版本：先执行 ads methodology import（PRD 4.6 / D10）"
        )
    return version


def get_param_group(session: Session, group: str, version_id: str | None = None) -> dict[str, Any]:
    if version_id is None:
        version_id = require_released_version(session).id
    rule = session.scalars(
        select(MethodologyRule).where(
            MethodologyRule.methodology_version_id == version_id,
            MethodologyRule.layer == "param",
            MethodologyRule.rule_code == group,
        )
    ).first()
    if rule is None or not rule.params_json:
        return {}
    return json.loads(rule.params_json)


def maturity_days(session: Session, ad_product_type: str, version_id: str | None = None) -> int:
    """HR-01/02：SP≥7 天、SB/SD≥14 天、混算从严。"""
    group = get_param_group(session, "attribution_maturity", version_id)
    sp_days = int(group.get("sp_min_days", 7))
    sb_days = int(group.get("sb_sd_min_days", 14))
    return sb_days if ad_product_type in {"sb", "sd"} else sp_days


def mixed_maturity_days(session: Session, version_id: str | None = None) -> int:
    """HR-02：混算从严取更严一侧。"""
    group = get_param_group(session, "attribution_maturity", version_id)
    return max(int(group.get("sp_min_days", 7)), int(group.get("sb_sd_min_days", 14)))


def t2_offset_days(session: Session, version_id: str | None = None) -> int:
    group = get_param_group(session, "data_window", version_id)
    return int(group.get("t2_offset_days", 2))


def default_target_acos(session: Session, version_id: str | None = None) -> float:
    group = get_param_group(session, "target_acos", version_id)
    return float(group.get("default", 0.25))
