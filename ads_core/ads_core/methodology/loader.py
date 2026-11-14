# -*- coding: utf-8 -*-
"""方法论导入：YAML → methodology_version + methodology_rule（设计 §5）。

幂等：同 version_no 已存在且 file_hash 一致 → 跳过；不一致 → 报错不覆盖（D6）。
校验：status 必须 released（draft 拒绝导入，PRD 4.6）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..storage.audit import write_audit
from ..storage.models import MethodologyRule, MethodologyVersion, now_iso


class MethodologyImportError(Exception):
    pass


@dataclass
class MethodologyImportResult:
    version_id: str
    version_no: str
    rule_count: int
    duplicate: bool = False


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _source_json(source: dict) -> str:
    return json.dumps(source, ensure_ascii=False)


def load_yaml(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def methodology_import(
    session: Session,
    yaml_path: str | Path,
    expected_status: str = "released",
    actor: str = "cli",
) -> MethodologyImportResult:
    path = Path(yaml_path)
    if not path.is_file():
        raise MethodologyImportError(f"文件不存在：{path}")
    data = load_yaml(path)
    meta = data.get("meta") or {}
    version_no = str(meta.get("version") or "")
    status = str(meta.get("status") or "")
    if not version_no:
        raise MethodologyImportError("meta.version 缺失")
    if status != expected_status:
        raise MethodologyImportError(
            f"方法论 status={status!r} ≠ 期望 {expected_status!r}（draft 拒绝发布导入，PRD 4.6）"
        )

    file_hash = _sha256(path)
    existing = session.scalars(
        select(MethodologyVersion).where(MethodologyVersion.version_no == version_no)
    ).first()
    if existing is not None:
        if existing.file_hash == file_hash:
            return MethodologyImportResult(existing.id, version_no, 0, duplicate=True)
        raise MethodologyImportError(
            f"版本 {version_no} 已存在且文件 hash 不一致（不静默覆盖，D6）；"
            f"库内={existing.file_hash[:12]}… 本次={file_hash[:12]}…"
        )

    version = MethodologyVersion(
        version_no=version_no,
        title=str(meta.get("methodology_title") or ""),
        status=status,
        source_type="notion",
        source_uri=(meta.get("extracted_from") or {}).get("notion_url"),
        file_hash=file_hash,
        reviewed_by=meta.get("reviewed_by"),
        approved_by=meta.get("approved_by"),
        released_at=now_iso(),
        summary=meta.get("one_line_conclusion"),
        created_at=now_iso(),
    )
    session.add(version)
    session.flush()

    count = 0
    for rule in data.get("hard_rules", []) or []:
        session.add(MethodologyRule(
            methodology_version_id=version.id,
            layer="hard",
            rule_code=str(rule.get("id") or f"HARD-{count + 1}"),
            name=rule.get("name"),
            statement=str(rule.get("statement") or ""),
            params_json=None,
            enforcement=rule.get("enforcement") or "null",
            source_json=_source_json(rule.get("source") or {}),
            created_at=now_iso(),
        ))
        count += 1

    for group_name, params in (data.get("parameters") or {}).items():
        session.add(MethodologyRule(
            methodology_version_id=version.id,
            layer="param",
            rule_code=str(group_name),
            name=str(group_name),
            statement=f"参数组 {group_name}",
            params_json=json.dumps(params, ensure_ascii=False),
            enforcement="ingest_gate",
            source_json=_source_json(params.get("source") or {}) if isinstance(params, dict) else "{}",
            created_at=now_iso(),
        ))
        count += 1

    for know in data.get("knowledge", []) or []:
        session.add(MethodologyRule(
            methodology_version_id=version.id,
            layer="knowledge",
            rule_code=str(know.get("id") or f"K-{count + 1}"),
            name=know.get("title"),
            statement=str(know.get("body") or ""),
            params_json=None,
            enforcement="null",
            source_json=_source_json(know.get("source") or {}),
            created_at=now_iso(),
        ))
        count += 1

    write_audit(session, actor, "methodology.import", "methodology_version", version.id,
                {"version_no": version_no, "rules": count, "file_hash": file_hash})
    return MethodologyImportResult(version.id, version_no, count)


def latest_released(session: Session) -> MethodologyVersion | None:
    versions = session.scalars(
        select(MethodologyVersion).where(MethodologyVersion.status == "released")
        .order_by(MethodologyVersion.created_at.desc())
    ).all()
    return versions[0] if versions else None
