# -*- coding: utf-8 -*-
"""D11 二期：领星 MCP JSON 适配器测试。

覆盖：JSON 读取（MCP 响应/存档两种形态）→ 识别（日粒度变体命中 / 窗口聚合拒绝）
→ 导入 → canonical → facts 全链；窗口聚合 JSON 的明确拒绝；真实存档 e2e 对齐
（与缺陷修复验证的 CSV 路径锚点对照）。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from ads_core.canonical import build_canonical
from ads_core.facts import build_facts
from ads_core.ingest import finalize_batch, ingest_file
from ads_core.ingest.detector import detect
from ads_core.ingest.reader import read_table
from ads_core.methodology import methodology_import
from ads_core.storage import (
    ImportBatch,
    MetricDaily,
    RawDataset,
    session_scope,
)

from .conftest import METHODOLOGY_YAML

ARCHIVE = (
    Path(__file__).resolve().parents[2] / "data" / "raw" / "6978_6979_validation_20261114"
    / "lingxing_ad_sp_campaign_report_daily_2026-11-02_2026-11-09.json"
)

TODAY = date(2026, 11, 14)


def _day(offset: int) -> str:
    return (TODAY - timedelta(days=offset)).isoformat()


def _mcp_wrapper(rows: list[dict]) -> dict:
    """领星 MCP 原始响应外壳（ad_sp_campaign_report 实测形态）。"""
    return {
        "code": 1, "msg": "操作成功", "success": True,
        "data": {"recordsFiltered": len(rows), "code": 0, "success": True,
                 "data": rows, "error_details": [], "next_token": None},
    }


def _daily_rows() -> list[dict]:
    """日粒度行（API 字段原名；无 campaign_name——与真实响应一致）。"""
    out = []
    for i, d in enumerate([_day(5), _day(4), _day(3)]):
        for j, cid in enumerate(("111111111111", "222222222222")):
            out.append({
                "report_date": d, "campaign_id": cid,
                "impressions": 1000 + i * 10 + j, "clicks": 20 + i + j,
                "cost": f"{10.5 + i:.2f}", "sales": f"{40 + i * 2:.2f}",
                "orders": 2 + (i % 2), "units": 2 + (i % 2),
                "profile_id": 1531098959787693, "targeting_type": None,
            })
    return out


def _window_rows() -> list[dict]:
    """窗口聚合行（ad_campaign_product_report 实测形态：无任何日期键）。"""
    return [{
        "campaign_name": "GJHIUBKNGT-AZDAuto", "campaign_id": "29437742665971",
        "sku": "GJHJUB-YCNGT-AA1-K", "asin": "B0H8YN7YX7", "ad_group_name": "Auto",
        "clicks": "17", "cost": None, "direct_sales": "17.81",
        "impressions": 748, "profile_id": 1531098959787693,
    }]


def _write_json(path: Path, obj) -> Path:
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return path


# ================================================================ reader


class TestJsonReader:
    def test_mcp_wrapper_shape(self, tmp_path):
        p = _write_json(tmp_path / "resp.json", _mcp_wrapper(_daily_rows()))
        t = read_table(p)
        assert len(t) == 6
        assert {"report_date", "campaign_id", "cost", "sales"} <= set(t.headers)

    def test_archive_shape(self, tmp_path):
        p = _write_json(tmp_path / "archive.json", {
            "provenance": {"source": "领星 MCP"}, "campaign_names": {"111": "C1"},
            "rows": _daily_rows(),
        })
        t = read_table(p)
        assert len(t) == 6

    def test_broken_json_raises(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="JSON 无法解析"):
            read_table(p)

    def test_non_tabular_json_empty(self, tmp_path):
        p = _write_json(tmp_path / "meta.json", {"run_log": "ok", "steps": 3})
        t = read_table(p)
        assert len(t) == 0


# ================================================================ detection


class TestJsonDetection:
    def test_daily_campaign_variant_hit(self):
        t = read_table(_write_json(Path(str(__import__("tempfile").mkdtemp())) / "d.json",
                                   _mcp_wrapper(_daily_rows())))
        det = detect(t.headers)
        assert det.detected_type == "campaign"
        assert det.variant == "lingxing_mcp_daily_campaign"
        assert det.confidence >= 0.85

    def test_window_aggregate_unknown(self):
        t = read_table(_write_json(Path(str(__import__("tempfile").mkdtemp())) / "w.json",
                                   _mcp_wrapper(_window_rows())))
        det = detect(t.headers)
        assert det.is_unknown


# ================================================================ e2e 合成


def _ingest_json(env, obj, name="resp.json"):
    p = _write_json(Path(env) / name, obj)
    with session_scope() as s:
        r = ingest_file(s, str(p), source_system="lingxing_mcp_json",
                        ad_product_type="sp", data_as_of=TODAY.isoformat(),
                        imported_by="test")
        s.commit()
    return r


class TestJsonIngestE2E:
    def test_full_chain(self, env):
        r = _ingest_json(env, _mcp_wrapper(_daily_rows()))
        assert r.status == "complete", [f.message for f in r.findings]
        assert r.detected_type == "campaign"
        assert r.data_start_date == _day(5) and r.data_end_date == _day(3)
        with session_scope() as s:
            build_canonical(s, r.batch_id, actor="test")
            s.commit()
            fr = build_facts(s, grains=["campaign"], actor="test")
            s.commit()
        assert fr.rows_written == 6
        with session_scope() as s:
            rows = s.scalars(select(MetricDaily).where(MetricDaily.grain == "campaign")).all()
            assert len(rows) == 6
            # campaign_name 允许为空（响应无此列），归属靠 campaign_id
            assert all(m.grain_keys_json and json.loads(m.grain_keys_json)["campaign_id"]
                       for m in rows)
            total_spend = round(sum(float(m.spend or 0) for m in rows), 2)
            assert total_spend == round((10.5 + 11.5 + 12.5) * 2, 2)  # 69.0
            # 全部行无映射 → product_id 为空（归属不明由观察层标注，不入产品粒度）
            assert all(m.product_id is None for m in rows)

    def test_window_aggregate_rejected_clearly(self, env):
        """窗口聚合 JSON：明确拒绝（缺日粒度），批次 failed，不静默入库。"""
        r = _ingest_json(env, _mcp_wrapper(_window_rows()), name="window.json")
        assert r.status == "failed"
        assert any(f.check_code == "QF-01" and f.severity == "error" for f in r.findings)
        with session_scope() as s:
            batch = s.get(ImportBatch, r.batch_id)
            assert batch.status == "failed"
            raw = s.scalars(select(RawDataset).where(
                RawDataset.batch_id == r.batch_id)).first()
            assert raw.status == "rejected"
            # 无事实行产生
            assert s.scalar(select(func.count()).select_from(MetricDaily)) == 0

    def test_adapter_missing_date_error(self, env):
        """行级兜底：即使 forced_type 绕过识别，缺日期行也会被适配器显式拒绝。"""
        from ads_core.adapters import AdapterError

        p = _write_json(Path(env) / "forced.json", _mcp_wrapper(_window_rows()))
        with session_scope() as s:
            r = ingest_file(s, str(p), source_system="lingxing_mcp_json",
                            ad_product_type="sp", forced_type="campaign",
                            data_as_of=TODAY.isoformat(), imported_by="test")
            s.commit()
        # canonical 构建阶段适配器行级拒绝（异常上抛，会话回滚，不产生 canonical/事实行）
        with pytest.raises(AdapterError, match="缺日粒度日期"):
            with session_scope() as s:
                build_canonical(s, r.batch_id, actor="test")
                s.commit()
        with session_scope() as s:
            assert s.scalar(select(func.count()).select_from(MetricDaily)) == 0


# ================================================================ 真实存档 e2e


@pytest.mark.skipif(not ARCHIVE.is_file(), reason="真实存档仅本机（gitignored）")
class TestRealArchiveE2E:
    """真实数据对齐：JSON 适配器路径 vs 缺陷修复验证的 CSV 路径锚点。"""

    def test_ingest_real_daily_json(self, env):
        with session_scope() as s:
            methodology_import(s, METHODOLOGY_YAML)
            s.commit()
        r = _ingest_json(env, json.loads(ARCHIVE.read_text(encoding="utf-8")),
                         name=ARCHIVE.name)
        assert r.status == "complete", [f.message for f in r.findings]
        assert r.row_count == 36
        assert r.data_start_date == "2026-11-02" and r.data_end_date == "2026-11-09"
        with session_scope() as s:
            build_canonical(s, r.batch_id, actor="test")
            s.commit()
            fr = build_facts(s, grains=["campaign"], actor="test")
            s.commit()
        assert fr.rows_written == 36
        with session_scope() as s:
            rows = s.scalars(select(MetricDaily).where(MetricDaily.grain == "campaign")).all()
        # 锚点 = 缺陷修复验证 CSV 路径结果（docs/defect-fix-20261114.md B3）
        assert round(sum(float(m.spend or 0) for m in rows), 2) == 175.88
        assert sum(int(m.clicks or 0) for m in rows) == 434
        assert sum(int(m.impressions or 0) for m in rows) == 44608
        assert sum(int(m.orders or 0) for m in rows) == 39
        assert round(sum(float(m.sales or 0) for m in rows), 2) == 684.00
        # 成熟度锚点：≤11-07 成熟 28 行，11-08/09 未成熟 8 行（取数日 2026-11-14）
        assert sum(1 for m in rows if m.maturity == "mature") == 28
        assert sum(1 for m in rows if m.maturity != "mature") == 8
