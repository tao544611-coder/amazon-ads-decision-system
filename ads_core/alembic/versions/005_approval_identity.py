# -*- coding: utf-8 -*-
"""缺陷修复迁移：approval 增加审批来源与身份验证标记（P1-03）。

Revision ID: 005_approval_identity
Revises: 004_lingxing_supplement
Create Date: 2026-11-14

兼容性说明：
- 仅新增两列，不改既有列与数据；历史行取默认值（channel=cli / identity_verified=0）。
- 幂等：003 迁移按当前模型 create_all 建表（新库已含新列），本迁移先查
  pragma table_info，列已存在则跳过；旧库（升级前建的 approval 表）正常补列。
- 旧版本代码读取新库不受影响（多余列被忽略）；新版本代码读取旧库须先升级到本迁移。
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "005_approval_identity"
down_revision = "004_lingxing_supplement"
branch_labels = None
depends_on = None


def _existing_columns(bind, table: str) -> set[str]:
    rows = bind.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def upgrade() -> None:
    bind = op.get_bind()
    cols = _existing_columns(bind, "approval")
    if "approval_channel" not in cols:
        op.add_column(
            "approval",
            sa.Column("approval_channel", sa.Text(), nullable=False, server_default="cli"),
        )
    if "identity_verified" not in cols:
        op.add_column(
            "approval",
            sa.Column("identity_verified", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    cols = _existing_columns(bind, "approval")
    if "identity_verified" in cols:
        op.drop_column("approval", "identity_verified")
    if "approval_channel" in cols:
        op.drop_column("approval", "approval_channel")
