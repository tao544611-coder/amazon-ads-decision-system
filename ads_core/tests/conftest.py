# -*- coding: utf-8 -*-
"""测试夹具：隔离 ADS_HOME + 迁移 + 方法论导入 + 合成报表工厂（§7）。"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from ads_core import config
from ads_core.ingest.importer import ingest_file
from ads_core.methodology import methodology_import
from ads_core.storage import reset_engine, session_scope

PROJECT_ROOT = Path(__file__).resolve().parents[1]
METHODOLOGY_YAML = PROJECT_ROOT.parent / "methodology" / "v1.0.0.yaml"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """隔离环境：独立 ADS_HOME → 迁移 → 导入方法论 v1.0.0。"""
    monkeypatch.setenv("ADS_HOME", str(tmp_path))
    monkeypatch.delenv("ADS_DB", raising=False)
    config.ensure_dirs()
    ini = PROJECT_ROOT / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.upgrade(cfg, "head")
    reset_engine()
    with session_scope() as s:
        methodology_import(s, METHODOLOGY_YAML)
        s.commit()
    yield tmp_path
    reset_engine()


@pytest.fixture()
def session(env):
    with session_scope() as s:
        yield s
        s.rollback()


# ---------------------------------------------------------------- 合成报表工厂（英文列名 SP bulk 风格）

HEADERS = {
    "campaign": [
        "Date", "Portfolio name", "Campaign Id", "Campaign Name", "Campaign Status",
        "Impressions", "Clicks", "Spend",
        "7 Day Total Orders (#)", "7 Day Total Sales", "7 Day Total Units (#)",
    ],
    "adgroup": [
        "Date", "Campaign Id", "Campaign Name", "Ad Group Id", "Ad Group Name",
        "Impressions", "Clicks", "Spend",
        "7 Day Total Orders (#)", "7 Day Total Sales", "7 Day Total Units (#)",
    ],
    "target": [
        "Date", "Campaign Id", "Ad Group Id", "Keyword or Product Targeting", "Match Type",
        "Keyword Id", "Impressions", "Clicks", "Spend",
        "7 Day Total Orders (#)", "7 Day Total Sales", "7 Day Total Units (#)",
    ],
    "searchterm": [
        "Date", "Campaign Id", "Ad Group Id", "Customer Search Term", "Keyword or Product Targeting",
        "Impressions", "Clicks", "Spend",
        "7 Day Total Orders (#)", "7 Day Total Sales", "7 Day Total Units (#)",
    ],
    "placement": [
        "Date", "Campaign Id", "Placement",
        "Impressions", "Clicks", "Spend",
        "7 Day Total Orders (#)", "7 Day Total Sales", "7 Day Total Units (#)",
    ],
    "advertised_product": [
        "Date", "Campaign Id", "Ad Group Id", "Advertised ASIN", "Advertised SKU",
        "Impressions", "Clicks", "Spend",
        "7 Day Total Orders (#)", "7 Day Total Sales", "7 Day Total Units (#)",
    ],
    "purchased_product": [
        "Date", "Campaign Id", "Advertised ASIN", "Purchased ASIN",
        "Impressions", "Clicks", "Spend",
        "7 Day Total Orders (#)", "7 Day Total Sales", "7 Day Total Units (#)",
    ],
}


def make_report(
    path: Path,
    report_type: str,
    rows: list[dict],
    extra_headers: list[str] | None = None,
    drop_headers: list[str] | None = None,
) -> Path:
    """生成合成 CSV 报表。rows 的键使用 HEADERS 中的列名。"""
    headers = list(HEADERS[report_type])
    if extra_headers:
        headers += extra_headers
    if drop_headers:
        headers = [h for h in headers if h not in drop_headers]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for row in rows:
            w.writerow([row.get(h, "") for h in headers])
    return path


def sample_campaign_rows(dates: list[str], campaign_id: str = "100000000000001") -> list[dict]:
    rows = []
    for i, d in enumerate(dates):
        rows.append({
            "Date": d,
            "Portfolio name": "Portfolio A",
            "Campaign Id": campaign_id,
            "Campaign Name": f"Campaign {campaign_id[-4:]}",
            "Campaign Status": "ENABLED",
            "Impressions": str(1000 + i * 10),
            "Clicks": str(20 + i),
            "Spend": f"{10.5 + i:.2f}",
            "7 Day Total Orders (#)": str(2 + (i % 2)),
            "7 Day Total Sales": f"{40 + i * 2:.2f}",
            "7 Day Total Units (#)": str(2 + (i % 2)),
        })
    return rows


@pytest.fixture()
def ingest(env):
    """便捷导入工厂。"""
    counter = {"n": 0}

    def _ingest(path, **kwargs):
        counter["n"] += 1
        with session_scope() as s:
            result = ingest_file(s, path, **kwargs)
            s.commit()
        return result

    return _ingest
