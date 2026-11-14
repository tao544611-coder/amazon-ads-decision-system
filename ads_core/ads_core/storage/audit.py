# -*- coding: utf-8 -*-
"""审计写入：service 入口统一落 audit_log（设计 §5）。"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from .models import AuditLog, now_iso


def write_audit(
    session: Session,
    actor: str,
    action: str,
    object_type: str | None = None,
    object_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        ts=now_iso(),
        actor=actor,
        action=action,
        object_type=object_type,
        object_id=object_id,
        details_json=json.dumps(details, ensure_ascii=False) if details else None,
    )
    session.add(entry)
    return entry
