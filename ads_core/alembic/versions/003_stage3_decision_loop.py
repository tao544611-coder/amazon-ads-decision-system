# -*- coding: utf-8 -*-
"""阶段三迁移：决策链 7 表 + 经验库 FTS5 虚表（D7：中文二元切分由代码层完成）。

Revision ID: 003_stage3_decision_loop
Revises: 002_stage2_analysis
Create Date: 2026-11-14
"""

from __future__ import annotations

from alembic import op

revision = "003_stage3_decision_loop"
down_revision = "002_stage2_analysis"
branch_labels = None
depends_on = None

TABLES = [
    "proposal", "approval", "action",
    "execution_receipt", "observation", "evaluation", "experience",
]


def upgrade() -> None:
    from ads_core.storage.models import Base

    bind = op.get_bind()
    Base.metadata.create_all(
        bind=bind, tables=[Base.metadata.tables[t] for t in TABLES]
    )
    op.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS experience_fts USING fts5("
        "exp_id UNINDEXED, text)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS experience_fts")
    from ads_core.storage.models import Base

    bind = op.get_bind()
    Base.metadata.drop_all(
        bind=bind, tables=[Base.metadata.tables[t] for t in TABLES]
    )
