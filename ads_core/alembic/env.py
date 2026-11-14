# -*- coding: utf-8 -*-
"""Alembic 环境：目标元数据 = ads_core.storage.Base.metadata。"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 保证未安装场景下也能从项目根导入
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ads_core import config  # noqa: E402
from ads_core.storage.models import Base  # noqa: E402

config_ = context.config
if config_.config_file_name is not None:
    fileConfig(config_.config_file_name)

if not config_.get_main_option("sqlalchemy.url"):
    config.ensure_dirs()
    db_url = f"sqlite:///{config.db_path()}"
    config_.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config_.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config_.get_section(config_.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
