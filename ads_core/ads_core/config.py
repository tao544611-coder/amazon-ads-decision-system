# -*- coding: utf-8 -*-
"""系统路径配置。

ADS_HOME 解析顺序：
1. 环境变量 ``ADS_HOME``（测试用 tmp_path 覆盖）；
2. 包目录向上两级（``<HOME>/ads_core/ads_core/config.py`` → ``<HOME>``）。

目录约定（设计 §8）：``<ADS_HOME>/data/raw`` 原始文件、``<ADS_HOME>/data/db`` 数据库。
"""

from __future__ import annotations

import os
from pathlib import Path


def ads_home() -> Path:
    env = os.environ.get("ADS_HOME")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    return ads_home() / "data"


def raw_dir() -> Path:
    return data_dir() / "raw"


def db_dir() -> Path:
    return data_dir() / "db"


def db_path() -> Path:
    override = os.environ.get("ADS_DB")
    if override:
        return Path(override).resolve()
    return db_dir() / "ads.sqlite"


def ensure_dirs() -> None:
    raw_dir().mkdir(parents=True, exist_ok=True)
    db_dir().mkdir(parents=True, exist_ok=True)
