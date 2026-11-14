# -*- coding: utf-8 -*-
"""分析上下文组装器（PRD 4.7 七步 · D3：引擎侧确定性组装，零 LLM）。

输出冻结快照 context_json + evidence 登记（T5 可追溯锚点）。
密钥/敏感配置永不进入上下文包（D9 白名单组装）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..events import events_in_window
from ..methodology import latest_released, require_released_version
from ..methodology.params import get_param_group
from ..storage.audit import write_audit
from ..storage.models import (
    AnalysisRun,
    BusinessEvent,
    CampaignMap,
    Evidence,
    KeywordRankSnapshot,
    MetricDaily,
    MethodologyRule,
    Product,
    ProductProfileVersion,
    now_iso,
)

CONTEXT_PARAM_GROUPS = [
    "target_acos", "zero_conversion", "bid_adjustment",
    "cvr_estimation", "acceptance", "attribution_maturity", "data_window",
]

CONVERSION_SIDE_METRICS = ("orders", "sales", "acos", "cvr")


@dataclass
class ContextResult:
    run_id: str
    status: str
    flags: dict
    evidence_count: int


class ContextError(Exception):
    pass


def _metric_row_payload(m: MetricDaily, keys: dict) -> dict:
    return {
        "fact_id": m.id,
        "date": m.date,
        "ad_product_type": m.ad_product_type,
        "maturity": m.maturity,
        "keys": keys,
        "impressions": m.impressions, "clicks": m.clicks, "spend": m.spend,
        "orders": m.orders, "sales": m.sales, "units": m.units,
        "ctr": m.ctr, "cpc": m.cpc, "cvr": m.cvr, "acos": m.acos, "roas": m.roas,
        "computable": json.loads(m.computable_json or "{}"),
    }


def _segment_compare(rows: list[MetricDaily], midpoint: str) -> dict:
    """窗口二等分对比（仅成熟行；PRD 4.7 第三步）。"""
    agg = {}
    for label, lo, hi in (
        ("first_half", None, midpoint),
        ("second_half", midpoint, None),
    ):
        bucket = agg.setdefault(label, {"impressions": 0, "clicks": 0, "spend": 0.0,
                                        "orders": 0, "sales": 0.0, "rows": 0})
        for m in rows:
            if m.maturity != "mature":
                continue
            if lo and m.date < lo:
                continue
            if hi and m.date >= hi:
                continue
            bucket["impressions"] += m.impressions or 0
            bucket["clicks"] += m.clicks or 0
            bucket["spend"] += m.spend or 0.0
            bucket["orders"] += m.orders or 0
            bucket["sales"] += m.sales or 0.0
            bucket["rows"] += 1
    for label, b in agg.items():
        b["acos"] = round(b["spend"] / b["sales"], 4) if b["sales"] else None
        b["cvr"] = round(b["orders"] / b["clicks"], 4) if b["clicks"] else None
        b["ctr"] = round(b["clicks"] / b["impressions"], 6) if b["impressions"] else None
    return agg


def _rank_history(rows: list[KeywordRankSnapshot]) -> list[dict]:
    """按关键词取最近两次快照对比（PRD 4.7 第三步：自然位变化）。"""
    by_kw: dict[str, list[KeywordRankSnapshot]] = {}
    for r in rows:
        by_kw.setdefault(r.keyword, []).append(r)
    out = []
    for kw, snaps in by_kw.items():
        snaps.sort(key=lambda s: s.captured_on)
        current = snaps[-1]
        previous = snaps[-2] if len(snaps) > 1 else None
        delta = (previous.organic_rank - current.organic_rank) if (
            previous and previous.organic_rank and current.organic_rank
        ) else None
        out.append({
            "rank_id": current.id,
            "keyword": kw,
            "current": {"captured_on": current.captured_on, "organic_rank": current.organic_rank,
                        "top_level": current.top_level},
            "previous": {"captured_on": previous.captured_on,
                         "organic_rank": previous.organic_rank} if previous else None,
            "delta_rank": delta,   # 正=提升（位次数字变小）
            "trend": ("up" if delta and delta > 0 else "down" if delta and delta < 0
                      else "stable" if delta == 0 else "unknown"),
        })
    return out


def _related_experience_safe(session: Session, sku: str) -> list[dict]:
    """相关经验检索（FTS5 表未建/为空时返回空，不阻断组装）。"""
    try:
        from ..experience import related_for_context

        return related_for_context(session, sku)
    except Exception:
        return []


def assemble_analysis_context(
    session: Session,
    sku: str,
    date_from: str,
    date_to: str,
    top_terms: int = 50,
    actor: str = "cli",
) -> ContextResult:
    # 前置：产品 + 档案 + 方法论（缺一即停，PRD 5.1「关键数据缺失必须暂停」）
    product = session.scalars(select(Product).where(Product.sku == sku)).first()
    if product is None:
        raise ContextError(f"SKU 不存在：{sku}（先 ads product add / profile-update）")
    profile = session.scalars(
        select(ProductProfileVersion)
        .where(ProductProfileVersion.product_id == product.id)
        .order_by(ProductProfileVersion.version_no.desc())
    ).first()
    if profile is None:
        raise ContextError(f"SKU {sku} 无产品档案版本（先 ads product profile-update）")
    methodology = require_released_version(session)

    # 该 SKU 的活动映射集（Id/名双键，与 facts 匹配口径一致）
    mappings = session.scalars(
        select(CampaignMap).where(CampaignMap.product_id == product.id)
    ).all()
    campaign_keys: set[str] = set()
    for cm in mappings:
        campaign_keys.add(cm.campaign_id)
        if cm.campaign_name:
            campaign_keys.add(cm.campaign_name)

    # 事实行：product 粒度 + campaign 粒度（映射集内）+ searchterm 粒度（映射集内 topN）
    in_window = select(MetricDaily).where(
        MetricDaily.date >= date_from, MetricDaily.date <= date_to
    )
    product_rows = session.scalars(
        in_window.where(MetricDaily.grain == "product", MetricDaily.product_id == product.id)
    ).all()
    campaign_rows = [
        m for m in session.scalars(
            in_window.where(MetricDaily.grain == "campaign")
        ).all()
        if json.loads(m.grain_keys_json).get("campaign_id") in campaign_keys
    ]
    searchterm_rows = sorted(
        (m for m in session.scalars(
            in_window.where(MetricDaily.grain == "searchterm")
        ).all()
         if json.loads(m.grain_keys_json).get("campaign_id") in campaign_keys),
        key=lambda m: -(m.spend or 0),
    )[:top_terms]

    facts_campaign = [_metric_row_payload(m, json.loads(m.grain_keys_json)) for m in campaign_rows]
    facts_searchterm = [_metric_row_payload(m, json.loads(m.grain_keys_json)) for m in searchterm_rows]
    facts_product = [_metric_row_payload(m, json.loads(m.grain_keys_json)) for m in product_rows]

    all_fact_rows = product_rows + campaign_rows + searchterm_rows
    mature = [m for m in all_fact_rows if m.maturity == "mature"]
    immature = [m for m in all_fact_rows if m.maturity != "mature"]
    immature_ratio = round(len(immature) / len(all_fact_rows), 4) if all_fact_rows else None

    midpoint = date.fromisoformat(date_from).toordinal()
    mid = date.fromordinal(midpoint + (date.fromisoformat(date_to).toordinal() - midpoint) // 2).isoformat()

    events = events_in_window(session, date_from, date_to)
    ranks = session.scalars(
        select(KeywordRankSnapshot)
        .where(KeywordRankSnapshot.product_id == product.id)
        .order_by(KeywordRankSnapshot.captured_on)
    ).all()

    rules = session.scalars(
        select(MethodologyRule).where(MethodologyRule.methodology_version_id == methodology.id)
    ).all()
    hard = [r for r in rules if r.layer == "hard"]
    params = {g: get_param_group(session, g, methodology.id) for g in CONTEXT_PARAM_GROUPS}
    knowledge = [r for r in rules if r.layer == "knowledge"]

    # 数据缺口标注（不虚构）
    data_gaps = []
    if not all_fact_rows:
        data_gaps.append("窗口内无该 SKU 相关事实行（映射活动无报表数据）")
    if not campaign_rows:
        data_gaps.append("campaign 粒度无数据（活动映射集与报表无交集）")

    flags = {
        "immature_ratio": immature_ratio,
        "mature_rows": len(mature),
        "immature_rows": len(immature),
        "confounding_events": [
            {"event_id": e.id, "event_type": e.event_type,
             "starts_at": e.starts_at, "ends_at": e.ends_at, "description": e.description}
            for e in events
        ],
        "data_gaps": data_gaps,
        "needs_manual_attention": bool(data_gaps),
    }

    context = {
        "meta": {
            "sku": sku, "asin": product.asin, "marketplace": product.marketplace,
            "date_range": {"from": date_from, "to": date_to, "midpoint": mid},
            "generated_at": now_iso(),
            "methodology_version_no": methodology.version_no,
            "profile_version_no": profile.version_no,
        },
        "data_condition": {
            "currency": "GBP",
            "maturity_policy": {
                "sp_min_days": params["attribution_maturity"].get("sp_min_days"),
                "sb_sd_min_days": params["attribution_maturity"].get("sb_sd_min_days"),
                "t2_offset_days": params["data_window"].get("t2_offset_days"),
            },
            "immature_policy": (
                "转化侧指标（orders/sales/acos/cvr）在 immature_observation 行不得作为正式结论依据（HR-01/02）；"
                "此类行已带 maturity 标注，comparison 仅使用成熟行"
            ),
        },
        "product_profile": json.loads(profile.payload_json),
        "facts": {
            "product_grain": facts_product,
            "campaign": facts_campaign,
            "searchterm_top": facts_searchterm,
            "comparison_campaign_mature_only": _segment_compare(campaign_rows, mid),
        },
        "keyword_ranks": _rank_history(list(ranks)),
        "business_events": flags["confounding_events"],
        "methodology": {
            "one_line_conclusion": methodology.summary,
            "hard_rules": [
                {"rule_code": r.rule_code, "name": r.name, "statement": r.statement,
                 "enforcement": r.enforcement}
                for r in hard
            ],
            "parameters": params,
            "knowledge": [
                {"code": r.rule_code, "title": r.name, "body": r.statement} for r in knowledge
            ],
        },
        "context_flags": flags,
        "related_experience": _related_experience_safe(session, sku),
        "judgment_requirements": {
            "conclusion_layers": ["已确认事实", "可能原因", "缺少证据的假设", "暂时无法判断"],
            "single_metric_ban": "不得以单一指标变化直接形成结论（PRD 4.7）；ACoS 不单独决定去留",
            "evidence_rule": "每条建议必须引用 fact_evidence_ids / event_evidence_ids（存在于本包），规则引用写 rule_code",
            "confounding_note": "窗口内存在活跃业务事件时，建议必须说明其对判断的影响（引用 event_id）",
        },
        "proposal_schema": {
            "required_fields": [
                "sku", "target", "current_performance", "problem", "fact_evidence_ids",
                "event_evidence_ids", "reasoning", "action", "adjustment", "expected_effect",
                "risks", "observation_window", "acceptance_criteria", "needs_approval",
            ],
            "note": "14 必填字段；evidence_ids 引用本包 fact_id/event_id；needs_approval 恒 true",
        },
    }

    batch_ids: set[str] = set()
    for m in all_fact_rows:
        for ref in json.loads(m.batch_refs_json or "[]"):
            if "batch_id" in ref:
                batch_ids.add(ref["batch_id"])

    run = AnalysisRun(
        sku=sku, date_from=date_from, date_to=date_to,
        marketplace=product.marketplace,
        status="context_ready",
        methodology_version_id=methodology.id,
        profile_version_id=profile.id,
        batch_ids_json=json.dumps(sorted(batch_ids), ensure_ascii=False),
        context_json=json.dumps(context, ensure_ascii=False),
        context_flags_json=json.dumps(flags, ensure_ascii=False),
        created_by=actor,
        created_at=now_iso(),
    )
    session.add(run)
    session.flush()

    # evidence 登记（T5 锚点）
    def _add_ev(etype: str, ref_id: str, snapshot: dict) -> None:
        session.add(Evidence(
            analysis_run_id=run.id, evidence_type=etype, ref_id=ref_id,
            snapshot_json=json.dumps(snapshot, ensure_ascii=False), created_at=now_iso(),
        ))

    for m in all_fact_rows:
        _add_ev("metric_daily", m.id, {
            "grain": m.grain, "date": m.date, "maturity": m.maturity,
            "spend": m.spend, "clicks": m.clicks, "orders": m.orders, "sales": m.sales,
        })
    for e in events:
        _add_ev("business_event", e.id, {
            "event_type": e.event_type, "starts_at": e.starts_at, "ends_at": e.ends_at,
        })
    for r in ranks:
        _add_ev("keyword_rank", r.id, {"keyword": r.keyword, "captured_on": r.captured_on,
                                       "organic_rank": r.organic_rank})
    for r in hard:
        _add_ev("methodology_rule", r.id, {"rule_code": r.rule_code, "layer": "hard"})
    _add_ev("product_profile", profile.id, {"version_no": profile.version_no})
    evidence_count = len(all_fact_rows) + len(events) + len(ranks) + len(hard) + 1

    write_audit(session, actor, "analysis.context", "analysis_run", run.id, {
        "sku": sku, "date_range": [date_from, date_to],
        "facts": len(all_fact_rows), "evidence": evidence_count,
        "confounding_events": len(events),
    })
    return ContextResult(run.id, run.status, flags, evidence_count)
