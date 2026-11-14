# -*- coding: utf-8 -*-
"""阶段六迁移（D11 一期）：evidence_type 白名单扩展 lingxing_mcp_supplement。

SQLite 无法原地修改 CHECK 约束 → 按新模型 DDL 重建 evidence 表并搬数据。
Revision ID: 004_lingxing_supplement
Revises: 003_stage3_decision_loop
Create Date: 2026-11-14
"""

from __future__ import annotations

from alembic import op

revision = "004_lingxing_supplement"
down_revision = "003_stage3_decision_loop"
branch_labels = None
depends_on = None

_COLS = "id, analysis_run_id, evidence_type, ref_id, snapshot_json, created_at"


def upgrade() -> None:
    from sqlalchemy.schema import CreateTable

    from ads_core.storage.models import Evidence

    ddl = str(CreateTable(Evidence.__table__).compile()).replace(
        "CREATE TABLE evidence", "CREATE TABLE evidence_new"
    )
    op.execute(ddl)
    op.execute(
        f"INSERT INTO evidence_new ({_COLS}) SELECT {_COLS} FROM evidence"
    )
    op.execute("DROP TABLE evidence")
    op.execute("ALTER TABLE evidence_new RENAME TO evidence")


def downgrade() -> None:
    # 回滚 = 恢复旧白名单（含 lingxing_mcp_supplement 的行会被旧 CHECK 拒收，需先清除）
    from sqlalchemy.schema import CreateTable

    from ads_core.storage.models import Evidence

    op.execute(
        "DELETE FROM evidence WHERE evidence_type = 'lingxing_mcp_supplement'"
    )
    old_ddl = str(CreateTable(Evidence.__table__).compile()).replace(
        "CREATE TABLE evidence", "CREATE TABLE evidence_old"
    )
    # 以新模型 DDL 建表后再替换为旧白名单
    old_ddl = old_ddl.replace(
        "IN ('metric_daily','business_event','keyword_rank',"
        "'methodology_rule','product_profile','lingxing_mcp_supplement')",
        "IN ('metric_daily','business_event','keyword_rank',"
        "'methodology_rule','product_profile')",
    )
    op.execute(old_ddl)
    op.execute(
        f"INSERT INTO evidence_old ({_COLS}) SELECT {_COLS} FROM evidence"
    )
    op.execute("DROP TABLE evidence")
    op.execute("ALTER TABLE evidence_old RENAME TO evidence")
