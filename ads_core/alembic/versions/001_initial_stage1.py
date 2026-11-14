# -*- coding: utf-8 -*-
"""阶段一初始迁移：20 张业务表 + 不可变触发器。

Revision ID: 001_initial_stage1
Revises:
Create Date: 2026-11-14

表结构来源：ads_core/storage/models.py（Base.metadata，设计 §1.2 DDL 级）。

触发器（设计 §1.2 系统域）：
- audit_log：禁 UPDATE + DELETE（append-only，PRD 7.3）；
- can_* 7 张 + metric_daily：禁 UPDATE + DELETE（修正=新批次，D6/PRD 4.1）；
- raw_dataset：禁 DELETE。status/superseded_by 为过程管理字段，QF-08 supersede
  流程需更新之（设计 §3.2「旧数据保留 + superseded_by 指向新版」），故放行
  UPDATE——这是对设计文档字面「raw_dataset 禁 UPDATE」的最小工程偏差，
  在交付文档中标注。
"""

from __future__ import annotations

from alembic import op

revision = "001_initial_stage1"
down_revision = None
branch_labels = None
depends_on = None

APPEND_ONLY_TABLES = [
    "can_campaign_daily",
    "can_adgroup_daily",
    "can_target_daily",
    "can_searchterm_daily",
    "can_placement_daily",
    "can_advertised_product_daily",
    "can_purchased_product_daily",
    "metric_daily",
]

DELETE_ONLY_TABLES = ["raw_dataset"]


def _drop_triggers(tables: list[str]) -> None:
    for t in tables:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{t}_no_update")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{t}_no_delete")


def upgrade() -> None:
    from ads_core.storage.models import Base

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)

    op.execute(
        "CREATE TRIGGER trg_audit_log_no_update BEFORE UPDATE ON audit_log "
        "BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_audit_log_no_delete BEFORE DELETE ON audit_log "
        "BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END"
    )
    for t in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{t}_no_update BEFORE UPDATE ON {t} "
            f"BEGIN SELECT RAISE(ABORT, '{t} is append-only (D6: 修正=新批次)'); END"
        )
        op.execute(
            f"CREATE TRIGGER trg_{t}_no_delete BEFORE DELETE ON {t} "
            f"BEGIN SELECT RAISE(ABORT, '{t} is append-only (D6: 修正=新批次)'); END"
        )
    for t in DELETE_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{t}_no_delete BEFORE DELETE ON {t} "
            f"BEGIN SELECT RAISE(ABORT, '{t} 禁删除（D6：数据不静默覆盖/不删）'); END"
        )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_no_update")
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_no_delete")
    _drop_triggers(APPEND_ONLY_TABLES + DELETE_ONLY_TABLES)

    from ads_core.storage.models import Base

    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
