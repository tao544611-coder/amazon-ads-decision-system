# -*- coding: utf-8 -*-
"""阶段二迁移：analysis_run + evidence（上下文组装与建议校验）。

Revision ID: 002_stage2_analysis
Revises: 001_initial_stage1
Create Date: 2026-11-14

设计依据：docs/stage2-detailed-design-v1.0.md §1（D3/D4/D5）。
"""

from __future__ import annotations

from alembic import op

revision = "002_stage2_analysis"
down_revision = "001_initial_stage1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from ads_core.storage.models import Base

    bind = op.get_bind()
    # 仅创建阶段二新增表（metadata 含全部表，create_all 按需检查存在性）
    Base.metadata.create_all(
        bind=bind,
        tables=[
            Base.metadata.tables["analysis_run"],
            Base.metadata.tables["evidence"],
        ],
    )


def downgrade() -> None:
    from ads_core.storage.models import Base

    bind = op.get_bind()
    Base.metadata.drop_all(
        bind=bind,
        tables=[
            Base.metadata.tables["evidence"],
            Base.metadata.tables["analysis_run"],
        ],
    )
