# -*- coding: utf-8 -*-
"""D11 一期测试：领星 ERP 每日明细适配 + MCP 补充证据（Gordon 2026-11-14 决议）。

覆盖：识别（变体命中/汇总拒绝/下划线归一）、适配器行级映射（混合类型/商品投放解析/
匹配方式归一）、端到端 ingest→canonical→facts、分析上下文补充证据段。
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from ads_core.analysis import assemble_analysis_context
from ads_core.analysis.lingxing_mcp import collect_lingxing_supplement
from ads_core.canonical import build_canonical
from ads_core.facts import build_facts
from ads_core.ingest.detector import detect, load_fingerprints, normalize_header
from ads_core.ingest.importer import ingest_file
from ads_core.ingest.reader import read_table
from ads_core.profile import map_campaign, update_profile, upsert_product
from ads_core.storage import (
    CanTargetDaily,
    Evidence,
    ImportBatch,
    session_scope,
)

from .conftest import make_report  # noqa: F401

TODAY = date(2026, 11, 14)

# 真实样本表头（每周投放报告(10.5--10.11)-每日明细 实测 24 列）
LINGXING_HEADERS = [
    "店铺名称", "国家", "类型", "广告组合", "广告活动", "广告组", "投放", "匹配方式",
    "日期", "备注", "曝光量", "IS", "点击", "CTR", "CPC-本币", "花费-本币",
    "广告销售额-本币", "ACoS", "ROAS", "广告订单", "CPA-本币", "CVR",
    "广告笔单价-本币", "广告销量",
]


def _lingxing_row(**over: str) -> list[str]:
    """按真实列序合成一行；关键词 SP 行为基底。"""
    base = [
        "主店铺MAIN-UK", "UK", "SP", "--", "TESTNGT-AZDAuto", "Auto",
        "portable food warmer", "词组", "2026-10-07", "",
        "100", "3.20%", "10", "10.00%", "0.5", "5.00",
        "50.00", "10.00%", "10.0", "2", "2.5", "20.00%", "25.0", "3",
    ]
    idx = {h: i for i, h in enumerate(LINGXING_HEADERS)}
    for k, v in over.items():
        base[idx[k]] = v
    return base


def make_lingxing_xlsx(path, rows: list[list[str]]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(LINGXING_HEADERS)
    for r in rows:
        ws.append(r)
    wb.save(path)
    wb.close()


# ---------------------------------------------------------------- 识别器

def test_normalize_underscore():
    assert normalize_header("ad_group") == "ad group"
    assert normalize_header("advertised_sku") == "advertised sku"
    assert normalize_header("Campaign Name") == "campaign name"


def test_detect_lingxing_daily_variant():
    r = detect(LINGXING_HEADERS)
    assert r.detected_type == "target"
    assert r.variant == "lingxing_daily_target"
    assert r.confidence >= 0.85
    assert r.attribution_window_days is None  # 无 'n day' 列


def test_detect_lingxing_summary_still_rejected():
    """汇总版（无「日期」列）按 D11 决议维持 unknown。"""
    kw = ["店铺名称", "国家", "类型", "广告组合", "广告活动", "广告组", "关键词",
          "匹配方式", "有效状态", "竞价-本币", "默认竞价-本币", "曝光量", "IS", "点击",
          "CTR", "CPC-本币", "花费-本币", "广告销售额-本币", "ACoS", "ROAS"]
    r = detect(kw)
    assert r.is_unknown

    pl = ["店铺名称", "国家", "类型", "广告组合", "广告活动", "广告位", "曝光量", "点击",
          "CTR", "CPC-本币", "花费-本币", "广告销售额-本币", "ACoS", "ROAS"]
    assert detect(pl).is_unknown


def test_detect_derived_csv_still_unknown():
    """本地派生 csv：下划线归一后必要列组仍差 campaign name → unknown（同源重复数据，决议不入）。"""
    headers = ["date", "campaign", "ad_group", "portfolio", "currency", "advertised_sku",
               "asin", "sku", "product", "impressions", "clicks", "spend", "sales",
               "orders", "units", "src_file", "src_folder"]
    r = detect(headers)
    assert r.is_unknown


def test_detect_console_headers_unaffected():
    """回归护栏：英文 bulk 表头不受 variants/下划线归一影响。"""
    headers = ["Date", "Campaign Id", "Campaign Name", "Ad Group Id",
               "Keyword or Product Targeting", "Match Type", "Keyword Id",
               "Impressions", "Clicks", "Spend"]
    r = detect(headers)
    assert r.detected_type == "target"
    assert r.variant is None


# ---------------------------------------------------------------- 适配器行级映射

def _adapter_rows(env):
    path = env / "lingxing.xlsx"
    make_lingxing_xlsx(path, [
        _lingxing_row(),                                                    # SP 关键词
        _lingxing_row(类型="SB", 广告组="SB", 投放="kids instant camera",
                     匹配方式="词组", 日期="2026-10-08"),                     # SB 关键词
        _lingxing_row(类型="SP", 广告活动="UK#manaul6941#ASIN", 广告组="ASIN",
                     投放='商品:"B0TEST0006"', 匹配方式="--", 日期="2026-10-09"),  # SP 商品投放
    ])
    table = read_table(path)
    from ads_core.adapters import ADAPTERS
    adapter = ADAPTERS["lingxing_erp"]()

    class _Raw:
        report_type = "target"
        marketplace = "UK"
        currency = "GBP"
        ad_product_type = "sp"
        source_system = "lingxing_erp"
        attribution_window_days = None

    return adapter.transform(_Raw(), table)


def test_adapter_row_mapping(env):
    rows = _adapter_rows(env)
    assert len(rows) == 3

    sp, sb, prod = rows
    # 行级类型分派（混合表核心）
    assert (sp.ad_product_type, sb.ad_product_type, prod.ad_product_type) == ("sp", "sb", "sp")
    # 业务键：活动名（O-1 约定）
    assert sp.campaign_id == "TESTNGT-AZDAuto"
    assert sp.campaign_name == sp.campaign_id
    # 投放文本/商品投放 ASIN 解析
    assert sp.target_text == "portable food warmer"
    assert prod.advertised_asin == "B0TEST0006"
    assert prod.target_text == '商品:"B0TEST0006"'
    # 匹配方式归一 + 原值留 attrs
    assert sp.match_type == "phrase"
    assert prod.match_type is None
    assert sp.attrs["__lingxing"]["match_type_raw"] == "词组"
    # 中文别名度量
    assert sp.impressions == 100 and sp.clicks == 10
    assert sp.spend == 5.0 and sp.sales == 50.0
    assert sp.orders == 2 and sp.units == 3
    # lineage 留痕
    assert sp.lineage["source_col_map"]["impressions"] == "曝光量"


def test_adapter_unknown_type_raises(env):
    path = env / "bad.xlsx"
    make_lingxing_xlsx(path, [_lingxing_row(类型="XX")])
    table = read_table(path)
    from ads_core.adapters import ADAPTERS
    adapter = ADAPTERS["lingxing_erp"]()

    class _Raw:
        report_type = "target"
        marketplace = "UK"
        currency = "GBP"
        ad_product_type = "sp"
        source_system = "lingxing_erp"
        attribution_window_days = None

    from ads_core.adapters.base import AdapterError
    with pytest.raises(AdapterError, match="未知「类型」列值"):
        adapter.transform(_Raw(), table)


# ---------------------------------------------------------------- 端到端 ingest → canonical → facts

def test_ingest_to_canonical_to_facts(env):
    path = env / "weekly.xlsx"
    make_lingxing_xlsx(path, [
        _lingxing_row(日期=f"2026-10-{d:02d}") for d in (5, 6, 7, 8, 9, 10, 11)
    ])
    with session_scope() as s:
        r = ingest_file(
            s, path, imported_by="test", source_type="manual_upload",
            source_system="lingxing_erp",
        )
        s.commit()
        assert r.status == "complete"
        assert r.detected_type == "target"
        batch_id = r.batch_id

    with session_scope() as s:
        b = s.get(ImportBatch, batch_id)
        assert b.source_system == "lingxing_erp"
        build_canonical(s, batch_id, actor="test")
        s.commit()
        targets = s.scalars(select(CanTargetDaily).where(CanTargetDaily.batch_id == batch_id)).all()
        assert len(targets) == 7
        assert all(t.ad_product_type == "sp" for t in targets)
        assert targets[0].campaign_id == "TESTNGT-AZDAuto"
        s.commit()

    # 映射活动 → facts target/campaign 粒度可消费
    with session_scope() as s:
        p = upsert_product(s, sku="6978", asin="B0TEST0001", actor="test")
        s.commit()
        map_campaign(s, campaign_id="TESTNGT-AZDAuto", product_id=p.product_id,
                     mapped_by="test", campaign_name="TESTNGT-AZDAuto", ad_product_type="sp")
        s.commit()
        result = build_facts(s, date_from="2026-10-05", date_to="2026-10-11", actor="test")
        s.commit()
        assert result.by_grain.get("target", 0) == 7


# ---------------------------------------------------------------- MCP 补充证据

def _make_mcp_week(root, week="2026-W45", start="2026-11-01", end="2026-11-07"):
    d = root / week
    (d / "6384").mkdir(parents=True)
    (d / "ad_campaign_report.json").write_text(json.dumps({
        "meta": {"report_date": f"{start} - {end}", "pulled_at": "2026-11-09 09:33:36",
                 "source": "lingxing MCP ad_campaign_report（只读）"},
        "data": [
            {"name": None, "impressions": "1000", "clicks": "50", "spends": "10.5",
             "sales": "100.0", "orders": "3"},
            {"name": "TESTNGT-AZDAuto", "impressions": "500", "clicks": "25",
             "spends": "5.25", "sales": "50.0", "orders": "2"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    (d / "6384" / "product_report_B0TEST0002.json").write_text(json.dumps({
        "current": [{"asin": "B0TEST0002", "sku": "GDIEB-YYNGT-AA1-K",
                     "campaign_name": "GDIEBKNGT-AZDAuto", "ad_group_name": "Auto",
                     "impressions": "12495", "clicks": "49", "spends": "42.33",
                     "sales": "116.6", "orders": "5"}],
        "previous": [],
    }, ensure_ascii=False), encoding="utf-8")


def test_supplement_overlap_and_match(env, monkeypatch):
    root = env / "mcp-raw"
    _make_mcp_week(root)
    monkeypatch.setenv("ADS_LINGXING_MCP_RAW", str(root))

    # 窗口重叠 → 命中；ASIN 匹配 → 商品行带出
    out = collect_lingxing_supplement("2026-11-01", "2026-11-07", {"B0TEST0002"})
    assert out["status"] == "ok"
    week = out["weeks"][0]
    assert week["report_date"] == "2026-11-01 - 2026-11-07"
    agg = week["store_campaign_aggregate"]
    assert agg["rows"] == 2 and agg["spends"] == 15.75 and agg["clicks"] == 75
    assert len(week["matched_products"]) == 1
    assert week["matched_products"][0]["phase"] == "current"

    # 窗口不重叠 → unavailable（不虚构）
    out2 = collect_lingxing_supplement("2026-10-01", "2026-10-15", {"B0TEST0002"})
    assert out2["status"] == "unavailable"


def test_supplement_missing_root(env, monkeypatch):
    monkeypatch.setenv("ADS_LINGXING_MCP_RAW", str(env / "no-such-dir"))
    out = collect_lingxing_supplement("2026-11-01", "2026-11-07", {"B0TEST0002"})
    assert out["status"] == "unavailable"


def test_context_includes_supplement(env, monkeypatch):
    """组装器集成：补充段进 context_json + evidence 登记。"""
    root = env / "mcp-raw"
    _make_mcp_week(root, start="2026-10-25", end="2026-10-31")
    monkeypatch.setenv("ADS_LINGXING_MCP_RAW", str(root))

    with session_scope() as s:
        p = upsert_product(s, sku="6978", asin="B0TEST0002", actor="test")
        s.commit()
        update_profile(
            s, p.product_id,
            {"A_basic": {"asin": "B0TEST0002"}, "B_operations": {"target_acos": 0.25}},
            reason="v1", changed_by="test",
        )
        s.commit()

        result = assemble_analysis_context(
            s, sku="6978", date_from="2026-10-25", date_to="2026-10-31", actor="test",
        )
        s.commit()
        assert result.status == "context_ready"

        from ads_core.storage import AnalysisRun
        run = s.get(AnalysisRun, result.run_id)
        ctx = json.loads(run.context_json)
        sup = ctx["lingxing_mcp_supplement"]
        assert sup["status"] == "ok"
        assert sup["weeks"][0]["report_date"] == "2026-10-25 - 2026-10-31"

        evs = s.scalars(select(Evidence).where(
            Evidence.analysis_run_id == result.run_id,
            Evidence.evidence_type == "lingxing_mcp_supplement",
        )).all()
        assert len(evs) == 1
