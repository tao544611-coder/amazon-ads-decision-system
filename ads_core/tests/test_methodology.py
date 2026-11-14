# -*- coding: utf-8 -*-
"""方法论导入测试（§7）：导入、版本唯一、重复幂等、draft 拒绝。"""

from __future__ import annotations

import copy
import yaml

from sqlalchemy import select

from ads_core.methodology import MethodologyImportError, methodology_import
from ads_core.storage import MethodologyRule, MethodologyVersion, session_scope

from .conftest import METHODOLOGY_YAML


def test_methodology_import_and_rules(env):
    with session_scope() as s:
        versions = s.scalars(select(MethodologyVersion)).all()
        assert len(versions) == 1  # conftest 已导入
        v = versions[0]
        assert v.version_no == "1.0.0"
        assert v.status == "released"
        rules = s.scalars(
            select(MethodologyRule).where(MethodologyRule.methodology_version_id == v.id)
        ).all()
        layers = {}
        for r in rules:
            layers[r.layer] = layers.get(r.layer, 0) + 1
        assert layers == {"hard": 19, "param": 9, "knowledge": 14}
        # 关键硬规则入库且带 enforcement
        hr01 = [r for r in rules if r.rule_code == "HR-01"]
        assert hr01 and hr01[0].enforcement == "ingest_gate"
        # 成熟度参数可读
        import json

        mat = [r for r in rules if r.layer == "param" and r.rule_code == "attribution_maturity"]
        assert mat
        params = json.loads(mat[0].params_json)
        assert params["sp_min_days"] == 7
        assert params["sb_sd_min_days"] == 14


def test_methodology_duplicate_idempotent(env):
    with session_scope() as s:
        r = methodology_import(s, METHODOLOGY_YAML)
        s.commit()
    assert r.duplicate is True
    assert r.rule_count == 0
    with session_scope() as s:
        assert len(s.scalars(select(MethodologyVersion)).all()) == 1


def test_methodology_conflicting_file_rejected(env, tmp_path):
    data = yaml.safe_load(METHODOLOGY_YAML.read_text(encoding="utf-8"))
    data["hard_rules"][0]["statement"] = "被篡改的规则内容"
    bad = tmp_path / "v1.0.0_tampered.yaml"
    bad.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with session_scope() as s:
        try:
            methodology_import(s, bad)
            raise AssertionError("hash 不一致应拒绝导入")
        except MethodologyImportError as e:
            assert "不静默覆盖" in str(e)
        s.rollback()


def test_methodology_draft_rejected(env, tmp_path):
    data = yaml.safe_load(METHODOLOGY_YAML.read_text(encoding="utf-8"))
    data["meta"]["version"] = "1.1.0-draft"
    data["meta"]["status"] = "draft"
    draft = tmp_path / "draft.yaml"
    draft.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with session_scope() as s:
        try:
            methodology_import(s, draft)
            raise AssertionError("draft 应拒绝导入")
        except MethodologyImportError as e:
            assert "draft 拒绝" in str(e) or "expected_status" in str(e)
        s.rollback()
