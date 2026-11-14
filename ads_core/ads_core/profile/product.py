# -*- coding: utf-8 -*-
"""产品与档案版本（SCD-2）、campaign 映射（设计 §1.2 产品域）。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..storage.audit import write_audit
from ..storage.models import (
    CampaignMap,
    Product,
    ProductProfileVersion,
    now_iso,
)


@dataclass
class ProductResult:
    product_id: str
    sku: str
    created: bool


def upsert_product(
    session: Session,
    sku: str,
    asin: str | None = None,
    title: str | None = None,
    marketplace: str = "UK",
    category: str | None = None,
    variants: list[str] | None = None,
    primary_variant: str | None = None,
    actor: str = "cli",
) -> ProductResult:
    existing = session.scalars(select(Product).where(Product.sku == sku)).first()
    if existing is not None:
        changed = False
        for field, val in (
            ("asin", asin), ("title", title), ("category", category),
            ("primary_variant", primary_variant),
        ):
            if val is not None and getattr(existing, field) != val:
                setattr(existing, field, val)
                changed = True
        if variants is not None:
            existing.variants_json = json.dumps(variants, ensure_ascii=False)
            changed = True
        if changed:
            existing.updated_at = now_iso()
            write_audit(session, actor, "product.update", "product", existing.id, {"sku": sku})
        return ProductResult(existing.id, sku, created=False)

    product = Product(
        sku=sku,
        asin=asin,
        title=title,
        marketplace=marketplace,
        category=category,
        variants_json=json.dumps(variants, ensure_ascii=False) if variants else None,
        primary_variant=primary_variant,
        status="active",
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    session.add(product)
    session.flush()
    write_audit(session, actor, "product.create", "product", product.id, {"sku": sku})
    return ProductResult(product.id, sku, created=True)


def _diff_payload(old: dict | None, new: dict) -> dict:
    """与上一版的差异（新增/修改/删除键级 diff）。"""
    if old is None:
        return {"added": sorted(new.keys()), "changed": {}, "removed": []}
    changed = {
        k: {"from": old.get(k), "to": new.get(k)}
        for k in set(old) | set(new)
        if old.get(k) != new.get(k)
    }
    return {
        "added": sorted(k for k in changed if k not in old),
        "changed": {k: v for k, v in changed.items() if k in old},
        "removed": sorted(k for k in changed if k not in new),
    }


def update_profile(
    session: Session,
    product_id: str,
    payload: dict,
    reason: str,
    changed_by: str,
    change_summary: str | None = None,
) -> ProductProfileVersion:
    """产品档案 SCD-2：每次更新生成新版本，diff 与上一版对比。"""
    product = session.get(Product, product_id)
    if product is None:
        raise ValueError(f"产品不存在：{product_id}")

    last = session.scalars(
        select(ProductProfileVersion)
        .where(ProductProfileVersion.product_id == product_id)
        .order_by(ProductProfileVersion.version_no.desc())
    ).first()
    next_no = (last.version_no + 1) if last else 1
    old_payload = json.loads(last.payload_json) if last else None

    version = ProductProfileVersion(
        product_id=product_id,
        version_no=next_no,
        payload_json=json.dumps(payload, ensure_ascii=False),
        change_summary=change_summary,
        change_reason=reason,
        changed_by=changed_by,
        diff_json=json.dumps(_diff_payload(old_payload, payload), ensure_ascii=False),
        effective_from=now_iso(),
        created_at=now_iso(),
    )
    session.add(version)
    session.flush()
    write_audit(session, changed_by, "profile.update", "product_profile_version", version.id,
                {"product_id": product_id, "version_no": next_no, "reason": reason})
    return version


def map_campaign(
    session: Session,
    campaign_id: str,
    product_id: str,
    mapped_by: str,
    campaign_name: str | None = None,
    ad_product_type: str | None = None,
    marketplace: str = "UK",
    note: str | None = None,
    effective_from: str | None = None,
) -> CampaignMap:
    """campaign → 产品 映射（历史可追溯：同键不同 effective_from 多行）。"""
    eff = effective_from or now_iso()
    existing = session.scalars(
        select(CampaignMap).where(
            CampaignMap.campaign_id == campaign_id,
            CampaignMap.ad_product_type == (ad_product_type or ""),
            CampaignMap.effective_from == eff,
        )
    ).first()
    if existing is not None:
        return existing
    entry = CampaignMap(
        campaign_id=campaign_id,
        campaign_name=campaign_name,
        marketplace=marketplace,
        ad_product_type=ad_product_type,
        product_id=product_id,
        mapped_by=mapped_by,
        confidence="confirmed",
        note=note,
        effective_from=eff,
        created_at=now_iso(),
    )
    session.add(entry)
    session.flush()
    write_audit(session, mapped_by, "campaign.map", "campaign_map", entry.id,
                {"campaign_id": campaign_id, "product_id": product_id})
    return entry
