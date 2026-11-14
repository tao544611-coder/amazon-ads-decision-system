# -*- coding: utf-8 -*-
"""T1 · 数据污染测试（§7）：重复导入 / 缺必要列 / 混币种 / 同报表改后重传。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from ads_core.ingest.importer import sha256_file
from ads_core.storage import (
    CanCampaignDaily,
    ImportBatch,
    QualityFinding,
    RawDataset,
    session_scope,
)

from .conftest import make_report, sample_campaign_rows

DATES = ["2026-10-01", "2026-10-02", "2026-10-03"]


def test_t1_1_duplicate_ingest_idempotent(env, ingest):
    """同文件重复导入：返回同一 batch、无新行、duplicate=True。"""
    f1 = make_report(env / "camp.csv", "campaign", sample_campaign_rows(DATES))
    r1 = ingest(f1)
    assert r1.duplicate is False

    with session_scope() as s:
        n_raw_before = len(s.scalars(select(RawDataset)).all())
        n_can_before = len(s.scalars(select(CanCampaignDaily)).all())

    r2 = ingest(f1)
    assert r2.duplicate is True
    assert r2.batch_id == r1.batch_id
    assert r2.raw_dataset_id == r1.raw_dataset_id

    with session_scope() as s:
        assert len(s.scalars(select(RawDataset)).all()) == n_raw_before
        assert len(s.scalars(select(CanCampaignDaily)).all()) == n_can_before
        assert len(s.scalars(select(ImportBatch)).all()) == 1


def test_t1_2_missing_required_columns_failed(env, ingest):
    """缺必要列（去掉 Spend 与 Clicks）：识别失败 → QF-01 error → 批次 failed。"""
    rows = sample_campaign_rows(DATES)
    f = make_report(env / "camp_broken.csv", "campaign", rows, drop_headers=["Spend", "Clicks"])
    r = ingest(f)
    assert r.status == "failed"
    codes = [x.check_code for x in r.findings]
    assert "QF-01" in codes
    with session_scope() as s:
        findings = s.scalars(
            select(QualityFinding).where(QualityFinding.check_code == "QF-01")
        ).all()
        assert findings and findings[0].severity == "error"


def test_t1_3_mixed_currency_rejected(env, ingest):
    """批内多币种：QF-05 error → 批次 failed，不进 canonical。"""
    rows = sample_campaign_rows(DATES)
    for row in rows[:2]:
        row["Currency"] = "GBP"
    rows[2]["Currency"] = "USD"
    f = make_report(env / "camp_currency.csv", "campaign", rows, extra_headers=["Currency"])
    r = ingest(f)
    assert r.status == "failed"
    assert "QF-05" in [x.check_code for x in r.findings]
    from ads_core.storage import CanCampaignDaily as _C

    with session_scope() as s:
        assert len(s.scalars(select(_C)).all()) == 0


def test_t1_5_reimport_after_rejected_batch(env, ingest):
    """failed 批次（raw rejected）后：同文件可修正重导（幂等唯一索引排除 rejected）。"""
    rows = sample_campaign_rows(DATES)
    # 先造一个会 failed 的版本：缺必要列 → QF-01 error → rejected
    f_bad = make_report(env / "camp_bad.csv", "campaign", rows, drop_headers=["Spend"])
    r_bad = ingest(f_bad)
    assert r_bad.status == "failed"
    with session_scope() as s:
        raw = s.get(RawDataset, r_bad.raw_dataset_id)
        assert raw.status == "rejected"

    # 修正后的文件（内容不同 → hash 不同）正常导入
    f_good = make_report(env / "camp_good.csv", "campaign", rows)
    r_good = ingest(f_good)
    assert r_good.duplicate is False
    assert r_good.status in {"complete", "needs_confirm"}

    # rejected 的同 hash 文件重导：允许重新登记（不走幂等返回）
    r_retry = ingest(f_bad, forced_type="campaign")
    assert r_retry.duplicate is False
    assert r_retry.raw_dataset_id != r_bad.raw_dataset_id


def test_t1_4_supersede_keeps_old_rows(env, ingest):
    """同报表新版本（同类型+同日期范围、不同 hash）：旧登记 superseded、旧 canonical 行完整保留。"""
    f1 = make_report(env / "camp_v1.csv", "campaign", sample_campaign_rows(DATES))
    r1 = ingest(f1)
    assert r1.status in {"complete", "needs_confirm"}
    if r1.status == "needs_confirm":
        # QF-04 无 gap、无重复 → complete；若触发 warn 人工确认（模拟运营确认）
        with session_scope() as s:
            b = s.get(ImportBatch, r1.batch_id)
            b.status = "complete"
            s.commit()

    from ads_core.canonical import build_canonical

    with session_scope() as s:
        build_canonical(s, r1.batch_id, actor="test")
        s.commit()
    with session_scope() as s:
        old_raw_id = s.scalars(
            select(RawDataset).where(RawDataset.batch_id == r1.batch_id)
        ).first().id
        old_rows = s.scalars(
            select(CanCampaignDaily).where(CanCampaignDaily.raw_dataset_id == old_raw_id)
        ).all()
        assert len(old_rows) == len(DATES)

    # 版本 2：同日期范围、数值不同
    rows_v2 = sample_campaign_rows(DATES)
    for row in rows_v2:
        row["Spend"] = "99.99"
    f2 = make_report(env / "camp_v2.csv", "campaign", rows_v2)
    r2 = ingest(f2)
    assert r2.detected_type == "campaign"
    assert "QF-08" in [x.check_code for x in r2.findings]
    assert any(sup["old_id"] == old_raw_id for sup in r2.superseded)

    with session_scope() as s:
        old = s.get(RawDataset, old_raw_id)
        assert old.status == "superseded"
        assert old.superseded_by == r2.raw_dataset_id
        # 旧 canonical 行完整保留（D6：不删）
        kept = s.scalars(
            select(CanCampaignDaily).where(CanCampaignDaily.raw_dataset_id == old_raw_id)
        ).all()
        assert len(kept) == len(DATES)
        # trace 链可查：新行指向新 raw，其 hash 与文件一致
        new_raw = s.get(RawDataset, r2.raw_dataset_id)
        assert new_raw.file_hash == sha256_file(f2)
