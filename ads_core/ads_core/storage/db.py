# -*- coding: utf-8 -*-
"""数据库连接：SQLite WAL + foreign_keys=ON（D2）。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .. import config


def make_engine(db_path: str | os.PathLike[str] | None = None) -> Engine:
    path = str(db_path) if db_path else config.db_path()
    engine = create_engine(f"sqlite:///{path}", future=True)
    _attach_pragmas(engine)
    return engine


def _attach_pragmas(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        config.ensure_dirs()
        _engine = make_engine()
    return _engine


def reset_engine() -> None:
    """测试用：重置进程级 engine 缓存。"""
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    eng = engine or get_engine()
    with Session(eng, expire_on_commit=False) as session:
        yield session
