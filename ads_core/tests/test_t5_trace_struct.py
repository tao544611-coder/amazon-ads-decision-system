# -*- coding: utf-8 -*-
"""T5 · 全链追溯测试（§7 / PRD 4.1）：事实行 → batch_refs → raw 文件 → hash。"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from ads_core.canonical import build_canonical
from ads_core.facts import build_facts
from ads_core.ingest.importer import sha256_file
from ads_core.storage import (
    CanCampaignDaily,
    ImportBatch,
    MetricDaily,
    RawDataset,
    session_scope,
)

from .conftest import make_report, sample_campaign_rows

DATES = ["2026-09-01", "2026-09-02"]


def test_t5_full_lineage(env, ingest):
    f = make_report(env / "camp.csv", "campaign", sample_campaign_rows(DATES))
    r = ingest(f)
    if r.status == "needs_confirm":
        with session_scope() as s:
            s.get(ImportBatch, r.batch_id).status = "complete"
            s.commit()
    with session_scope() as s:
        build_canonical(s, r.batch_id, actor="test")
        s.commit()
    with session_scope() as s:
        build_facts(s, batch_id=r.batch_id, grains=["campaign"], actor="test")
        s.commit()

    with session_scope() as s:
        facts = s.scalars(select(MetricDaily).where(MetricDaily.grain == "campaign")).all()
        assert facts
        for m in facts:
            refs = json.loads(m.batch_refs_json)
            assert refs, "batch_refs 为空"
            for ref in refs:
                batch = s.get(ImportBatch, ref["batch_id"])
                assert batch is not None
                raw = s.get(RawDataset, ref["raw_dataset_id"])
                assert raw is not None
                # raw 文件在盘且 hash 一致
                stored = Path(raw.stored_path)
                assert stored.is_file(), f"原始文件缺失：{stored}"
                assert sha256_file(stored) == raw.file_hash
                # canonical 行存在且指向同一 raw
                cans = s.scalars(
                    select(CanCampaignDaily).where(
                        CanCampaignDaily.raw_dataset_id == raw.id,
                        CanCampaignDaily.date == m.date,
                    )
                ).all()
                assert cans
        # 事实行挂接方法论版本
        assert all(m.methodology_version_id for m in facts)
        assert all(m.formula_version == "1.0.0" for m in facts)
