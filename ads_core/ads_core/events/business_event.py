# -*- coding: utf-8 -*-
"""业务事件与关键词排名快照（阶段一手动/Agent 录入，CLI 落地）。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..storage.audit import write_audit
from ..storage.models import BusinessEvent, KeywordRankSnapshot, now_iso


def add_business_event(
    session: Session,
    event_type: str,
    starts_at: str,
    description: str,
    recorded_by: str,
    product_id: str | None = None,
    asin: str | None = None,
    ends_at: str | None = None,
    data_source: str | None = None,
    impact_scope: str | None = None,
) -> BusinessEvent:
    if product_id is None and asin is None:
        raise ValueError("product_id 与 asin 至少其一（设计 §1.2）")
    event = BusinessEvent(
        event_type=event_type,
        product_id=product_id,
        asin=asin,
        starts_at=starts_at,
        ends_at=ends_at,
        description=description,
        data_source=data_source,
        recorded_by=recorded_by,
        impact_scope=impact_scope,
        created_at=now_iso(),
    )
    session.add(event)
    session.flush()
    write_audit(session, recorded_by, "event.add", "business_event", event.id,
                {"event_type": event_type, "starts_at": starts_at})
    return event


def events_in_window(session: Session, date_from: str, date_to: str) -> list[BusinessEvent]:
    """窗口查询：事件区间与查询窗有交集。"""
    return list(
        session.scalars(
            select(BusinessEvent).where(
                BusinessEvent.starts_at <= date_to,
                (BusinessEvent.ends_at.is_(None)) | (BusinessEvent.ends_at >= date_from),
            )
        ).all()
    )


def add_keyword_rank(
    session: Session,
    product_id: str,
    keyword: str,
    captured_on: str,
    recorded_by: str,
    organic_rank: int | None = None,
    top_level: int | None = None,
    marketplace: str = "UK",
    source: str = "manual",
    data_as_of: str | None = None,
    notes: str | None = None,
) -> KeywordRankSnapshot:
    snap = KeywordRankSnapshot(
        product_id=product_id,
        keyword=keyword,
        marketplace=marketplace,
        captured_on=captured_on,
        organic_rank=organic_rank,
        top_level=top_level,
        source=source,
        data_as_of=data_as_of,
        recorded_by=recorded_by,
        notes=notes,
        created_at=now_iso(),
    )
    session.add(snap)
    session.flush()
    write_audit(session, recorded_by, "rank.add", "keyword_rank_snapshot", snap.id,
                {"keyword": keyword, "captured_on": captured_on})
    return snap
