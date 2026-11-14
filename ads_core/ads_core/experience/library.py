# -*- coding: utf-8 -*-
"""经验库（PRD 4.11）：FTS5 检索（D7 中文二元切分）+ 失败经验永不删除。

历史案例只是辅助依据，不覆盖正式方法论（包内标注）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..storage.audit import write_audit
from ..storage.models import Experience, Product, now_iso

STATUS_VALUES = {"verified", "pending_verification", "failed"}
STRENGTH_VALUES = {"strong", "medium", "weak"}


class ExperienceError(Exception):
    pass


def _bigram(s: str) -> str:
    """中文按字符二元切分（D7：无需分词器）；英文词原样。"""
    tokens: list[str] = re.findall(r"[A-Za-z0-9]+", s)
    for chunk in re.findall(r"[\u4e00-\u9fff]+", s):
        if len(chunk) == 1:
            tokens.append(chunk)
        else:
            tokens.extend(chunk[i:i + 2] for i in range(len(chunk) - 1))
    return " ".join(tokens)


def _fts_text(exp: dict) -> str:
    parts = [
        exp.get("scenario", ""), exp.get("original_problem", ""),
        exp.get("ai_judgment", "") or "", exp.get("operator_correction", "") or "",
        exp.get("actual_action", "") or "", exp.get("observation_result", "") or "",
    ]
    return _bigram(" ".join(parts))


def add_experience(
    session: Session,
    scenario: str,
    original_problem: str,
    created_by: str,
    sku: str | None = None,
    ai_judgment: str | None = None,
    operator_correction: str | None = None,
    actual_action: str | None = None,
    observation_result: str | None = None,
    evidence_strength: str = "medium",
    status: str = "pending_verification",
    applicable_when: list[str] | None = None,
    not_applicable_when: list[str] | None = None,
    related_action_id: str | None = None,
    exp_code: str | None = None,
    actor: str = "cli",
) -> Experience:
    if status not in STATUS_VALUES:
        raise ExperienceError(f"status 须为 {'|'.join(sorted(STATUS_VALUES))}")
    if evidence_strength not in STRENGTH_VALUES:
        raise ExperienceError(f"evidence_strength 须为 {'|'.join(sorted(STRENGTH_VALUES))}")

    product_id = None
    if sku:
        product = session.scalars(select(Product).where(Product.sku == sku)).first()
        if product is None:
            raise ExperienceError(f"SKU 不存在：{sku}")
        product_id = product.id

    code = exp_code or f"EXP-{date.today().isoformat()}-{_next_seq(session)}"
    exp = Experience(
        exp_code=code,
        product_id=product_id,
        scenario=scenario,
        original_problem=original_problem,
        ai_judgment=ai_judgment,
        operator_correction=operator_correction,
        actual_action=actual_action,
        observation_result=observation_result,
        evidence_strength=evidence_strength,
        applicable_when_json=json.dumps(applicable_when or [], ensure_ascii=False),
        not_applicable_when_json=json.dumps(not_applicable_when or [], ensure_ascii=False),
        status=status,
        related_action_id=related_action_id,
        created_by=created_by,
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    session.add(exp)
    session.flush()
    # FTS 双写
    session.execute(
        text("INSERT INTO experience_fts (exp_id, text) VALUES (:id, :t)"),
        {"id": exp.id, "t": _fts_text({
            "scenario": scenario, "original_problem": original_problem,
            "ai_judgment": ai_judgment, "operator_correction": operator_correction,
            "actual_action": actual_action, "observation_result": observation_result,
        })},
    )
    write_audit(session, actor, "experience.add", "experience", exp.id,
                {"exp_code": code, "status": status})
    return exp


def _next_seq(session: Session) -> str:
    today_prefix = f"EXP-{date.today().isoformat()}"
    codes = [
        c for (c,) in session.execute(
            select(Experience.exp_code).where(Experience.exp_code.like(f"{today_prefix}-%"))
        ).all()
    ]
    n = 0
    for c in codes:
        m = re.search(r"-(\d+)$", c)
        if m:
            n = max(n, int(m.group(1)))
    return f"{n + 1:03d}"


@dataclass
class SearchHit:
    exp_code: str
    scenario: str
    status: str
    rank: float


def search_experience(session: Session, query: str, limit: int = 5) -> list[SearchHit]:
    """FTS5 检索（中文二元切分查询，token 间 OR——召回优先，rank 排序）。"""
    tokens = _bigram(query).split()
    if not tokens:
        return []
    match_expr = " OR ".join(f'"{t}"' for t in tokens)
    rows = session.execute(
        text(
            "SELECT e.exp_code, e.scenario, e.status, rank "
            "FROM experience_fts f JOIN experience e ON e.id = f.exp_id "
            "WHERE experience_fts MATCH :q ORDER BY rank LIMIT :lim"
        ),
        {"q": match_expr, "lim": limit},
    ).all()
    return [SearchHit(r[0], r[1], r[2], float(r[3])) for r in rows]


def related_for_context(session: Session, sku: str, limit: int = 5) -> list[dict]:
    """上下文包接入：SKU 相关经验（辅助依据，不覆盖方法论，PRD 4.11）。"""
    hits = search_experience(session, sku, limit=limit)
    out = []
    for h in hits:
        exp = session.scalars(
            select(Experience).where(Experience.exp_code == h.exp_code)
        ).first()
        if exp is None:
            continue
        out.append({
            "exp_code": exp.exp_code, "scenario": exp.scenario,
            "original_problem": exp.original_problem, "status": exp.status,
            "evidence_strength": exp.evidence_strength,
            "observation_result": exp.observation_result,
            "usage_note": "历史案例为辅助依据，不得覆盖正式方法论（PRD 4.11）",
        })
    return out
