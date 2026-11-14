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


def lingxing_mcp_raw_root() -> Path:
    """领星 MCP 周检原始数据根目录（D11 一期：分析侧补充证据，不进事实层）。

    环境变量 ``ADS_LINGXING_MCP_RAW`` 优先；默认指向 Amazon_Work 周检链
    ``projects/auto-ads-check/data/raw``（每周一自动落盘 <YYYY-Www>/）。
    目录不存在时返回路径本身（调用方判 exists）。
    """
    override = os.environ.get("ADS_LINGXING_MCP_RAW")
    if override:
        return Path(override).resolve()
    return Path(
        r"D:\000GordonZou\Amazon_Work\projects\auto-ads-check\data\raw"
    )


def ensure_dirs() -> None:
    raw_dir().mkdir(parents=True, exist_ok=True)
    db_dir().mkdir(parents=True, exist_ok=True)
