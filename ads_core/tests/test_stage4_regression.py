# -*- coding: utf-8 -*-
"""阶段四：真实样本全链回归测试（samples/ 不在本机时全组 skip）。

回归锚点 = 2026-11-14 首次真实冒烟结果。任何断言失败 = 引擎行为漂移。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from ads_core.analysis import assemble_analysis_context
from ads_core.canonical import build_canonical
from ads_core.facts import build_facts
from ads_core.ingest import finalize_batch, ingest_file
from ads_core.methodology import methodology_import
from ads_core.profile import map_campaign, update_profile, upsert_product
from ads_core.storage import (
    ImportBatch,
    MetricDaily,
    RawDataset,
    session_scope,
)
from ads_core.storage.models import CANONICAL_TABLES

from .conftest import METHODOLOGY_YAML

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "raw-reports"

pytestmark = pytest.mark.skipif(
    not SAMPLES.is_dir(), reason="samples/raw-reports 不在本机（真实样本仅本地回归）"
)

# 首次冒烟锚点（2026-11-14；注：早期文档曾写 8287 系汇总笔误，
# 依各批 canonical build 实际输出 7781+2+73+337+306+93 核定为 8592）
ANCHOR_CANONICAL_TOTAL = 8592
ANCHOR_FACTS_TOTAL = 8499
ANCHOR_IMMATURE_RATIO = 0.0757


def _ingest_and_finalize(env, paths, **kwargs) -> str:
    batch_id = None
    with session_scope() as s:
        for p in paths:
            r = ingest_file(s, p, defer_finalize=True, batch_id=batch_id, **kwargs)
            batch_id = batch_id or r.batch_id
        finalize_batch(s, batch_id)
        s.commit()
    return batch_id


@pytest.fixture(scope="module", autouse=True)
def _regression_env(tmp_path_factory):
    """模块级真实样本回归环境（一次搭建，全组复用；直接操作 environ 避免 scope 限制）。"""
    import os

    tmp = tmp_path_factory.mktemp("stage4_reg")
    old_home = os.environ.get("ADS_HOME")
    old_db = os.environ.get("ADS_DB")
    os.environ["ADS_HOME"] = str(tmp)
    os.environ.pop("ADS_DB", None)
    from ads_core import config
    from alembic import command
    from alembic.config import Config

    config.ensure_dirs()
    ini = Path(__file__).resolve().parents[1] / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(ini.parent / "alembic"))
    command.upgrade(cfg, "head")
    from ads_core.storage import reset_engine

    reset_engine()
    with session_scope() as s:
        methodology_import(s, METHODOLOGY_YAML)
        s.commit()
    yield tmp
    reset_engine()
    if old_home is None:
        os.environ.pop("ADS_HOME", None)
    else:
        os.environ["ADS_HOME"] = old_home
    if old_db is not None:
        os.environ["ADS_DB"] = old_db


def test_reg_1_ingest_batches(_regression_env):
    """9 份样本 → 5 批 complete + 1 批 failed（不支持源拦截）。"""
    b1 = _ingest_and_finalize(_regression_env, [
        SAMPLES / "20261112_Sponsored_Products_Targeting_report (3).xlsx",
        SAMPLES / "20261112_Sponsored_Products_Search_term_report (3).xlsx",
        SAMPLES / "20261112_Sponsored_Products_Advertised_product_report (3).xlsx",
    ], imported_by="regression")
    b2 = _ingest_and_finalize(_regression_env, [SAMPLES / "Sponsored_Products_Campaign_report.csv"],
                              ad_product_type="sp", imported_by="regression")
    b3 = _ingest_and_finalize(_regression_env, [SAMPLES / "Sponsored_Brands_Campaign_report.xlsx"],
                              ad_product_type="sb", imported_by="regression")
    b4 = _ingest_and_finalize(_regression_env, [SAMPLES / "Sponsored_Products_Search_term_report (1).xlsx"],
                              imported_by="regression")
    b5 = _ingest_and_finalize(_regression_env, [
        SAMPLES / "关键词报告(8.22--9.19)-汇总-2101946964824051713.xlsx",
        SAMPLES / "广告位报告(8.22--9.19)-汇总-2101940911973609473.xlsx",
        SAMPLES / "advertised_product_3SKU_明细.csv",
    ], imported_by="regression")

    with session_scope() as s:
        statuses = {b.id: b.status for b in s.scalars(select(ImportBatch)).all()}
    assert statuses[b1] == "complete"
    assert statuses[b2] == "complete"
    assert statuses[b3] == "complete"
    assert statuses[b4] == "complete"
    assert statuses[b5] == "failed"


def test_reg_2_canonical_rowcount(_regression_env):
    with session_scope() as s:
        for b in s.scalars(select(ImportBatch).where(ImportBatch.status.in_(("complete", "partial")))).all():
            build_canonical(s, b.id, actor="regression")
        s.commit()
        total = 0
        for model in CANONICAL_TABLES.values():
            total += s.scalar(select(func.count()).select_from(model))
    assert total == ANCHOR_CANONICAL_TOTAL, "canonical 总行数与首次冒烟锚点漂移"


def test_reg_3_facts_rowcount_and_maturity(_regression_env):
    with session_scope() as s:
        result = build_facts(s, actor="regression")
        s.commit()
    assert result.rows_written == ANCHOR_FACTS_TOTAL
    assert result.qf10["immature_ratio"] == ANCHOR_IMMATURE_RATIO


def test_reg_4_context_structure(_regression_env):
    """上下文包结构完整（结构键回归，防组装器漂移）。"""
    with session_scope() as s:
        p = upsert_product(s, sku="6978", actor="regression")
        s.commit()
        update_profile(s, p.product_id, {"A_basic": {"asin": "B0TEST0001"}}, reason="reg", changed_by="reg")
        s.commit()
        map_campaign(s, campaign_id="111111111111", product_id=p.product_id,
                     mapped_by="reg", campaign_name="TESTNGT-AZDAuto", ad_product_type="sp")
        s.commit()
        run = assemble_analysis_context(
            s, sku="6978", date_from="2025-12-01", date_to="2026-01-05", actor="regression",
        )
        s.commit()
    ctx = _load_ctx(run.run_id)
    for key in ("meta", "data_condition", "product_profile", "facts", "keyword_ranks",
                "business_events", "methodology", "context_flags", "related_experience",
                "judgment_requirements", "proposal_schema"):
        assert key in ctx, f"上下文缺键：{key}"
    assert ctx["methodology"]["parameters"]["attribution_maturity"]["sp_min_days"] == 7
    assert ctx["meta"]["methodology_version_no"] == "1.0.0"


def _load_ctx(run_id: str) -> dict:
    from ads_core.storage import AnalysisRun

    with session_scope() as s:
        return json.loads(s.get(AnalysisRun, run_id).context_json)
