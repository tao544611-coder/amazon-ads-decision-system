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
    # 领星 MCP 补充证据固定指向不存在目录：测试确定性与本机真实数据解耦
    monkeypatch.setenv("ADS_LINGXING_MCP_RAW", str(tmp_path / "lingxing-missing"))
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


# ---------------------------------------------------------------- 阶段三链路共享 fixture

@pytest.fixture()
def approved_proposal(env):
    """产品+档案+映射+事实+上下文+通过校验的建议+审批 → (proposal_id, approval_id)。"""
    from datetime import date, timedelta

    from ads_core.analysis import assemble_analysis_context, validate_proposals
    from ads_core.canonical import build_canonical
    from ads_core.decision import approve_proposal, submit_proposals
    from ads_core.facts import build_facts
    from ads_core.ingest.importer import ingest_file
    from ads_core.profile import map_campaign, update_profile, upsert_product
    from ads_core.storage import ImportBatch, MetricDaily, session_scope
    from sqlalchemy import select

    today = date(2026, 11, 14)
    mature_day = (today - timedelta(days=20)).isoformat()

    with session_scope() as s:
        p = upsert_product(s, sku="6978", actor="test")
        s.commit()
        update_profile(
            s, p.product_id,
            {"A_basic": {"asin": "B0TEST0001"}, "B_operations": {"target_acos": 0.25}},
            reason="v1", changed_by="test",
        )
        s.commit()
        map_campaign(s, campaign_id="111111111111", product_id=p.product_id,
                     mapped_by="test", campaign_name="TESTNGT-AZDAuto",
                     ad_product_type="sp")
        s.commit()

    rows = sample_campaign_rows([mature_day], campaign_id="111111111111")
    for r in rows:
        r["Campaign Name"] = "TESTNGT-AZDAuto"
    f = make_report(env / "shared_camp.csv", "campaign", rows)
    with session_scope() as s:
        r = ingest_file(s, f, data_as_of=today.isoformat())
        s.commit()
    if r.status == "needs_confirm":
        with session_scope() as s:
            s.get(ImportBatch, r.batch_id).status = "complete"
            s.commit()
    with session_scope() as s:
        build_canonical(s, r.batch_id, actor="test")
        s.commit()
    with session_scope() as s:
        build_facts(s, grains=["campaign", "product"], actor="test")
        s.commit()

    with session_scope() as s:
        run = assemble_analysis_context(
            s, sku="6978",
            date_from=(today - timedelta(days=40)).isoformat(),
            date_to=today.isoformat(), actor="test",
        )
        s.commit()
        fact = s.scalars(
            select(MetricDaily).where(MetricDaily.grain == "campaign")
        ).first()
        proposal_dict = {
            "sku": "6978", "target": "TESTNGT-AZDAuto",
            "current_performance": "ACOS 30%", "problem": "高于目标",
            "fact_evidence_ids": [fact.id], "event_evidence_ids": [],
            "reasoning": "成熟期 ACOS 偏高",
            "action": "adjust_bid", "adjustment": "-10%", "new_bid": 0.60,
            "expected_effect": "ACOS 收敛至 25%", "risks": "无",
            "observation_window": "7 天", "acceptance_criteria": "ACOS ≤ 25%",
            "needs_approval": True,
        }
        verdicts = validate_proposals(s, run.run_id, [proposal_dict], actor="test")
        s.commit()
    assert verdicts[0].verdict == "passed", verdicts[0].violations

    with session_scope() as s:
        submitted = submit_proposals(s, run.run_id, actor="test")
        s.commit()
    assert len(submitted.created) == 1
    proposal_id = submitted.created[0]
    with session_scope() as s:
        approval = approve_proposal(s, proposal_id, "approve", decided_by="Gordon")
        s.commit()
    return proposal_id, approval.id
