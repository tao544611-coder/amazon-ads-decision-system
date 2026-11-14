# -*- coding: utf-8 -*-
"""数据模型（阶段一 · 20 张业务表 + alembic_version）。

约定（设计 §1.2）：PK=id TEXT(uuid4)；时间戳 TEXT ISO8601 UTC；金额/数值 REAL；
枚举 TEXT+CHECK；所有表带 created_at。audit_log 及 raw_dataset/can_*/metric_daily
禁 UPDATE/DELETE 由 Alembic 迁移中的触发器保证。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    MetaData,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def now_iso() -> str:
    # 微秒精度：同事务内多次创建（如连续审批）仍可按时间稳定排序
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# ---------------------------------------------------------------- 产品域


class Product(Base):
    __tablename__ = "product"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    sku: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    asin: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    marketplace: Mapped[str] = mapped_column(Text, default="UK", nullable=False)
    category: Mapped[str | None] = mapped_column(Text)
    variants_json: Mapped[str | None] = mapped_column(Text)
    primary_variant: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="active", nullable=False)
    created_at: Mapped[str] = mapped_column(Text, default=now_iso, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, default=now_iso, onupdate=now_iso)


class ProductProfileVersion(Base):
    __tablename__ = "product_profile_version"
    __table_args__ = (UniqueConstraint("product_id", "version_no", name="uq_product_version"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    product_id: Mapped[str] = mapped_column(
        ForeignKey("product.id"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    change_summary: Mapped[str | None] = mapped_column(Text)
    change_reason: Mapped[str] = mapped_column(Text, nullable=False)
    changed_by: Mapped[str] = mapped_column(Text, nullable=False)
    diff_json: Mapped[str | None] = mapped_column(Text)
    effective_from: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, default=now_iso, nullable=False)


class CampaignMap(Base):
    __tablename__ = "campaign_map"
    __table_args__ = (
        UniqueConstraint("campaign_id", "ad_product_type", "effective_from", name="uq_campaign_map"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(Text, nullable=False)
    campaign_name: Mapped[str | None] = mapped_column(Text)
    marketplace: Mapped[str] = mapped_column(Text, default="UK", nullable=False)
    ad_product_type: Mapped[str | None] = mapped_column(Text)
    product_id: Mapped[str] = mapped_column(ForeignKey("product.id"), nullable=False)
    mapped_by: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str] = mapped_column(Text, default="confirmed", nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    effective_from: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, default=now_iso, nullable=False)


# ---------------------------------------------------------------- 事件域


class BusinessEvent(Base):
    __tablename__ = "business_event"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    product_id: Mapped[str | None] = mapped_column(ForeignKey("product.id"))
    asin: Mapped[str | None] = mapped_column(Text)
    starts_at: Mapped[str] = mapped_column(Text, nullable=False)
    ends_at: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    data_source: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[str] = mapped_column(Text, nullable=False)
    impact_scope: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, default=now_iso, nullable=False)
    __table_args__ = (
        CheckConstraint(
            "product_id IS NOT NULL OR asin IS NOT NULL", name="product_or_asin"
        ),
    )


# ---------------------------------------------------------------- 数据管道域


class ImportBatch(Base):
    __tablename__ = "import_batch"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    imported_at: Mapped[str] = mapped_column(Text, nullable=False)
    imported_by: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_system: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    summary_json: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, default=now_iso, nullable=False)
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','identified','quality_running',"
            "'complete','partial','incomplete','needs_confirm','failed')",
            name="batch_status",
        ),
    )


class RawDataset(Base):
    __tablename__ = "raw_dataset"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("import_batch.id"), nullable=False
    )
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    # 幂等唯一由 __table_args__ 部分唯一索引保证（rejected 除外）
    file_hash: Mapped[str] = mapped_column(Text, nullable=False)
    file_size: Mapped[int | None] = mapped_column()
    row_count: Mapped[int | None] = mapped_column()
    report_type: Mapped[str | None] = mapped_column(Text)
    ad_product_type: Mapped[str | None] = mapped_column(Text)
    marketplace: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str | None] = mapped_column(Text)
    attribution_window_days: Mapped[int | None] = mapped_column()
    data_start_date: Mapped[str | None] = mapped_column(Text)
    data_end_date: Mapped[str | None] = mapped_column(Text)
    data_as_of: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="registered", nullable=False)
    superseded_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, default=now_iso, nullable=False)
    __table_args__ = (
        # file_hash 幂等唯一（T1/D6），rejected 登记除外：误参数批次的文件可换参数重导
        Index(
            "uq_raw_dataset_file_hash",
            "file_hash",
            unique=True,
            sqlite_where=text("status != 'rejected'"),
        ),
        CheckConstraint(
            "report_type IS NULL OR report_type IN ('campaign','adgroup','target',"
            "'searchterm','placement','advertised_product','purchased_product','unknown')",
            name="report_type",
        ),
        CheckConstraint(
            "ad_product_type IS NULL OR ad_product_type IN ('sp','sb','sd','unknown')",
            name="ad_product_type",
        ),
        CheckConstraint(
            "status IN ('registered','identified','normalized','superseded','rejected')",
            name="raw_status",
        ),
    )


class ReportIdentification(Base):
    __tablename__ = "report_identification"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    raw_dataset_id: Mapped[str] = mapped_column(
        ForeignKey("raw_dataset.id"), nullable=False
    )
    detected_type: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column()
    matched_columns_json: Mapped[str | None] = mapped_column(Text)
    missing_required_json: Mapped[str | None] = mapped_column(Text)
    ambiguities_json: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, default=now_iso, nullable=False)


class QualityFinding(Base):
    __tablename__ = "quality_finding"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(ForeignKey("import_batch.id"), nullable=False)
    raw_dataset_id: Mapped[str | None] = mapped_column(ForeignKey("raw_dataset.id"))
    check_code: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, default=now_iso, nullable=False)
    __table_args__ = (
        CheckConstraint("severity IN ('error','warn','info')", name="severity"),
    )


# ---------------------------------------------------------------- canonical 层（7 张同构表）


class _CanonicalBase(Base):
    __abstract__ = True

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(ForeignKey("import_batch.id"), nullable=False)
    raw_dataset_id: Mapped[str] = mapped_column(
        ForeignKey("raw_dataset.id"), nullable=False
    )
    date: Mapped[str] = mapped_column(Text, nullable=False)
    marketplace: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    ad_product_type: Mapped[str] = mapped_column(Text, nullable=False)
    attribution_window_days: Mapped[int | None] = mapped_column()
    campaign_id: Mapped[str] = mapped_column(Text, nullable=False)
    campaign_name: Mapped[str | None] = mapped_column(Text)
    campaign_status: Mapped[str | None] = mapped_column(Text)
    portfolio_name: Mapped[str | None] = mapped_column(Text)
    impressions: Mapped[int | None] = mapped_column()
    clicks: Mapped[int | None] = mapped_column()
    spend: Mapped[float | None] = mapped_column()
    orders: Mapped[int | None] = mapped_column()
    sales: Mapped[float | None] = mapped_column()
    units: Mapped[int | None] = mapped_column()
    attrs_json: Mapped[str | None] = mapped_column(Text)
    source_lineage_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, default=now_iso, nullable=False)
    __table_args__ = (
        CheckConstraint("ad_product_type IN ('sp','sb','sd')", name="ad_product_type"),
    )


class CanCampaignDaily(_CanonicalBase):
    __tablename__ = "can_campaign_daily"
    __table_args__ = (
        UniqueConstraint("batch_id", "date", "campaign_id", name="uq_can_campaign"),
    )


class CanAdgroupDaily(_CanonicalBase):
    __tablename__ = "can_adgroup_daily"
    __table_args__ = (
        UniqueConstraint(
            "batch_id", "date", "campaign_id", "adgroup_id", name="uq_can_adgroup"
        ),
    )
    adgroup_id: Mapped[str | None] = mapped_column(Text)
    adgroup_name: Mapped[str | None] = mapped_column(Text)


class CanTargetDaily(_CanonicalBase):
    __tablename__ = "can_target_daily"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "date",
            "campaign_id",
            "adgroup_id",
            "target_id",
            name="uq_can_target",
        ),
    )
    adgroup_id: Mapped[str | None] = mapped_column(Text)
    adgroup_name: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    target_text: Mapped[str | None] = mapped_column(Text)
    match_type: Mapped[str | None] = mapped_column(Text)


class CanSearchtermDaily(_CanonicalBase):
    __tablename__ = "can_searchterm_daily"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "date",
            "campaign_id",
            "adgroup_id",
            "target_id",
            "search_term",
            name="uq_can_searchterm",
        ),
    )
    adgroup_id: Mapped[str | None] = mapped_column(Text)
    adgroup_name: Mapped[str | None] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(Text)
    target_text: Mapped[str | None] = mapped_column(Text)
    match_type: Mapped[str | None] = mapped_column(Text)
    search_term: Mapped[str | None] = mapped_column(Text)


class CanPlacementDaily(_CanonicalBase):
    __tablename__ = "can_placement_daily"
    __table_args__ = (
        UniqueConstraint(
            "batch_id", "date", "campaign_id", "placement", name="uq_can_placement"
        ),
    )
    placement: Mapped[str | None] = mapped_column(Text)


class CanAdvertisedProductDaily(_CanonicalBase):
    __tablename__ = "can_advertised_product_daily"
    __table_args__ = (
        # 校准（真实样本 20261112）：同 (date,campaign,adgroup,asin) 存在多 SKU 行，
        # UNIQUE 须含 advertised_sku，否则真实数据写入撞唯一约束
        UniqueConstraint(
            "batch_id",
            "date",
            "campaign_id",
            "adgroup_id",
            "advertised_asin",
            "advertised_sku",
            name="uq_can_adv_product",
        ),
    )
    adgroup_id: Mapped[str | None] = mapped_column(Text)
    advertised_asin: Mapped[str | None] = mapped_column(Text)
    advertised_sku: Mapped[str | None] = mapped_column(Text)


class CanPurchasedProductDaily(_CanonicalBase):
    __tablename__ = "can_purchased_product_daily"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "date",
            "campaign_id",
            "adgroup_id",
            "advertised_asin",
            "advertised_sku",
            "purchased_asin",
            name="uq_can_pur_product",
        ),
    )
    adgroup_id: Mapped[str | None] = mapped_column(Text)
    advertised_asin: Mapped[str | None] = mapped_column(Text)
    advertised_sku: Mapped[str | None] = mapped_column(Text)
    purchased_asin: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------- 事实层


class MetricDaily(Base):
    __tablename__ = "metric_daily"
    __table_args__ = (
        UniqueConstraint(
            "grain",
            "grain_keys_hash",
            "date",
            "ad_product_type",
            "methodology_version_id",
            name="uq_metric_daily",
        ),
        CheckConstraint(
            "grain IN ('campaign','adgroup','target','searchterm','placement','product')",
            name="grain",
        ),
        CheckConstraint(
            "maturity IN ('mature','immature_observation')", name="maturity"
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    grain: Mapped[str] = mapped_column(Text, nullable=False)
    grain_keys_json: Mapped[str] = mapped_column(Text, nullable=False)
    grain_keys_hash: Mapped[str] = mapped_column(Text, nullable=False)
    product_id: Mapped[str | None] = mapped_column(ForeignKey("product.id"))
    date: Mapped[str] = mapped_column(Text, nullable=False)
    marketplace: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    ad_product_type: Mapped[str] = mapped_column(Text, nullable=False)
    impressions: Mapped[int | None] = mapped_column()
    clicks: Mapped[int | None] = mapped_column()
    spend: Mapped[float | None] = mapped_column()
    orders: Mapped[int | None] = mapped_column()
    sales: Mapped[float | None] = mapped_column()
    units: Mapped[int | None] = mapped_column()
    ctr: Mapped[float | None] = mapped_column()
    cpc: Mapped[float | None] = mapped_column()
    cvr: Mapped[float | None] = mapped_column()
    acos: Mapped[float | None] = mapped_column()
    roas: Mapped[float | None] = mapped_column()
    cpa: Mapped[float | None] = mapped_column()
    organic_order_ratio: Mapped[float | None] = mapped_column()
    tacos: Mapped[float | None] = mapped_column()
    gross_profit: Mapped[float | None] = mapped_column()
    ad_profit: Mapped[float | None] = mapped_column()
    computable_json: Mapped[str] = mapped_column(Text, nullable=False)
    maturity: Mapped[str] = mapped_column(Text, nullable=False)
    batch_refs_json: Mapped[str] = mapped_column(Text, nullable=False)
    methodology_version_id: Mapped[str] = mapped_column(
        ForeignKey("methodology_version.id"), nullable=False
    )
    formula_version: Mapped[str] = mapped_column(Text, nullable=False)
    computed_at: Mapped[str] = mapped_column(Text, nullable=False)


class KeywordRankSnapshot(Base):
    __tablename__ = "keyword_rank_snapshot"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    product_id: Mapped[str] = mapped_column(ForeignKey("product.id"), nullable=False)
    keyword: Mapped[str] = mapped_column(Text, nullable=False)
    marketplace: Mapped[str] = mapped_column(Text, default="UK", nullable=False)
    captured_on: Mapped[str] = mapped_column(Text, nullable=False)
    organic_rank: Mapped[int | None] = mapped_column()
    top_level: Mapped[int | None] = mapped_column()
    source: Mapped[str] = mapped_column(Text, nullable=False)
    data_as_of: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, default=now_iso, nullable=False)
    __table_args__ = (
        CheckConstraint("source IN ('manual','agent')", name="source"),
    )


# ---------------------------------------------------------------- 方法论域


class MethodologyVersion(Base):
    __tablename__ = "methodology_version"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    version_no: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, default="notion", nullable=False)
    source_uri: Mapped[str | None] = mapped_column(Text)
    file_hash: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(Text)
    released_at: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, default=now_iso, nullable=False)
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','released','deprecated')", name="mv_status"
        ),
    )


class MethodologyRule(Base):
    __tablename__ = "methodology_rule"
    __table_args__ = (
        UniqueConstraint("methodology_version_id", "rule_code", name="uq_rule_code"),
        CheckConstraint(
            "layer IN ('hard','param','knowledge')", name="rule_layer"
        ),
        CheckConstraint(
            "enforcement IN ('ingest_gate','validator','acceptance','null')",
            name="rule_enforcement",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    methodology_version_id: Mapped[str] = mapped_column(
        ForeignKey("methodology_version.id"), nullable=False
    )
    layer: Mapped[str] = mapped_column(Text, nullable=False)
    rule_code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    params_json: Mapped[str | None] = mapped_column(Text)
    enforcement: Mapped[str | None] = mapped_column(Text, default="null")
    source_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, default=now_iso, nullable=False)


# ---------------------------------------------------------------- 分析域（阶段二）


class AnalysisRun(Base):
    __tablename__ = "analysis_run"
    __table_args__ = (
        CheckConstraint(
            "status IN ('context_ready','validating','proposals_validated',"
            "'validated_none','failed')",
            name="run_status",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    date_from: Mapped[str] = mapped_column(Text, nullable=False)
    date_to: Mapped[str] = mapped_column(Text, nullable=False)
    marketplace: Mapped[str] = mapped_column(Text, default="UK", nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="context_ready")
    methodology_version_id: Mapped[str] = mapped_column(
        ForeignKey("methodology_version.id"), nullable=False
    )
    profile_version_id: Mapped[str] = mapped_column(
        ForeignKey("product_profile_version.id"), nullable=False
    )
    batch_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    context_json: Mapped[str] = mapped_column(Text, nullable=False)
    context_flags_json: Mapped[str | None] = mapped_column(Text)
    proposals_json: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, default=now_iso, nullable=False)
    completed_at: Mapped[str | None] = mapped_column(Text)


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "evidence_type", "ref_id", name="uq_evidence"),
        CheckConstraint(
            "evidence_type IN ('metric_daily','business_event','keyword_rank',"
            "'methodology_rule','product_profile')",
            name="evidence_type",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_run.id"), nullable=False
    )
    evidence_type: Mapped[str] = mapped_column(Text, nullable=False)
    ref_id: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, default=now_iso, nullable=False)


# ---------------------------------------------------------------- 决策链域（阶段三）


class Proposal(Base):
    __tablename__ = "proposal"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "proposal_index", name="uq_proposal_run_index"),
        CheckConstraint(
            "status IN ('pending_approval','approved','approved_modified','rejected','deferred')",
            name="proposal_status",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_run.id"), nullable=False
    )
    proposal_index: Mapped[int] = mapped_column(nullable=False)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    current_value_json: Mapped[str | None] = mapped_column(Text)
    proposed_value_json: Mapped[str | None] = mapped_column(Text)
    adjustment: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    methodology_version_id: Mapped[str] = mapped_column(
        ForeignKey("methodology_version.id"), nullable=False
    )
    profile_version_id: Mapped[str] = mapped_column(
        ForeignKey("product_profile_version.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending_approval")
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, default=now_iso, nullable=False)


class Approval(Base):
    __tablename__ = "approval"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('approve','modify_approve','reject','defer')", name="approval_decision"
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("proposal.id"), nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[str] = mapped_column(Text, nullable=False)
    final_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    self_approval: Mapped[int] = mapped_column(default=0, nullable=False)  # O3-1 留痕
    created_at: Mapped[str] = mapped_column(Text, default=now_iso, nullable=False)


class Action(Base):
    __tablename__ = "action"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','executing','executed','execution_failed',"
            "'rolled_back','cancelled')",
            name="action_status",
        ),
        CheckConstraint("execute_mode IN ('simulated','manual')", name="action_mode"),
        CheckConstraint(
            "verify_status IN ('unverified','verified','state_unknown')", name="action_verify"
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("proposal.id"), nullable=False)
    approval_id: Mapped[str] = mapped_column(ForeignKey("approval.id"), nullable=False)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_json: Mapped[str | None] = mapped_column(Text)
    original_value_json: Mapped[str | None] = mapped_column(Text)
    final_value_json: Mapped[str | None] = mapped_column(Text)
    execute_mode: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    approved_by: Mapped[str] = mapped_column(Text, nullable=False)
    executor: Mapped[str] = mapped_column(Text, nullable=False)
    executed_at: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[str | None] = mapped_column(Text)
    verify_status: Mapped[str] = mapped_column(Text, nullable=False, default="unverified")
    rollback_policy_json: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, default=now_iso, nullable=False)


class ExecutionReceipt(Base):
    __tablename__ = "execution_receipt"
    __table_args__ = (
        CheckConstraint(
            "receipt_type IN ('simulated','manual','agent')", name="receipt_type"
        ),
        CheckConstraint("result IN ('success','failed','unknown')", name="receipt_result"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    action_id: Mapped[str] = mapped_column(ForeignKey("action.id"), nullable=False)
    receipt_type: Mapped[str] = mapped_column(Text, nullable=False)
    operator: Mapped[str] = mapped_column(Text, nullable=False)
    operated_at: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[str] = mapped_column(Text, nullable=False)
    before_state_json: Mapped[str | None] = mapped_column(Text)
    after_state_json: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, default=now_iso, nullable=False)


class Observation(Base):
    __tablename__ = "observation"
    __table_args__ = (
        UniqueConstraint("action_id", name="uq_observation_action"),
        CheckConstraint(
            "status IN ('open','window_complete','evaluated')", name="obs_status"
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    action_id: Mapped[str] = mapped_column(ForeignKey("action.id"), nullable=False)
    window_start: Mapped[str] = mapped_column(Text, nullable=False)
    window_end: Mapped[str] = mapped_column(Text, nullable=False)
    baseline_json: Mapped[str] = mapped_column(Text, nullable=False)
    post_data_json: Mapped[str | None] = mapped_column(Text)
    concurrent_events_json: Mapped[str | None] = mapped_column(Text)
    concurrent_actions_json: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")
    created_at: Mapped[str] = mapped_column(Text, default=now_iso, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, default=now_iso, onupdate=now_iso)


class Evaluation(Base):
    __tablename__ = "evaluation"
    __table_args__ = (
        UniqueConstraint("action_id", name="uq_evaluation_action"),
        CheckConstraint(
            "verdict IN ('valid','invalid','negative','insufficient_evidence')",
            name="eval_verdict",
        ),
        CheckConstraint(
            "evidence_strength IN ('strong','medium','weak')", name="eval_strength"
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    action_id: Mapped[str] = mapped_column(ForeignKey("action.id"), nullable=False)
    observation_id: Mapped[str] = mapped_column(ForeignKey("observation.id"), nullable=False)
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    attribution_json: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids_json: Mapped[str | None] = mapped_column(Text)
    maturity_note: Mapped[str | None] = mapped_column(Text)
    evidence_strength: Mapped[str] = mapped_column(Text, nullable=False, default="medium")
    rollback_suggested: Mapped[int] = mapped_column(default=0, nullable=False)
    decided_by: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, default=now_iso, nullable=False)


class Experience(Base):
    __tablename__ = "experience"
    __table_args__ = (
        UniqueConstraint("exp_code", name="uq_experience_code"),
        CheckConstraint(
            "status IN ('verified','pending_verification','failed')", name="exp_status"
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    exp_code: Mapped[str] = mapped_column(Text, nullable=False)
    product_id: Mapped[str | None] = mapped_column(ForeignKey("product.id"))
    scenario: Mapped[str] = mapped_column(Text, nullable=False)
    original_problem: Mapped[str] = mapped_column(Text, nullable=False)
    ai_judgment: Mapped[str | None] = mapped_column(Text)
    operator_correction: Mapped[str | None] = mapped_column(Text)
    actual_action: Mapped[str | None] = mapped_column(Text)
    observation_result: Mapped[str | None] = mapped_column(Text)
    evidence_strength: Mapped[str] = mapped_column(Text, nullable=False, default="medium")
    applicable_when_json: Mapped[str | None] = mapped_column(Text)
    not_applicable_when_json: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending_verification")
    related_action_id: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, default=now_iso, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, default=now_iso, onupdate=now_iso)


# ---------------------------------------------------------------- 系统域


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=new_id)
    ts: Mapped[str] = mapped_column(Text, nullable=False, default=now_iso)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    object_type: Mapped[str | None] = mapped_column(Text)
    object_id: Mapped[str | None] = mapped_column(Text)
    details_json: Mapped[str | None] = mapped_column(Text)


CANONICAL_TABLES: dict[str, type[_CanonicalBase]] = {
    "campaign": CanCampaignDaily,
    "adgroup": CanAdgroupDaily,
    "target": CanTargetDaily,
    "searchterm": CanSearchtermDaily,
    "placement": CanPlacementDaily,
    "advertised_product": CanAdvertisedProductDaily,
    "purchased_product": CanPurchasedProductDaily,
}
