# -*- coding: utf-8 -*-
"""缺陷修复回归测试（2026-11-14 任务单 P0-01/P0-02/P1-01/P1-02/P1-03）。

P0-01 观察窗口 SKU 数据隔离 / P0-02 同期动作识别 /
P1-01 指标引用判断与出价拆分 / P1-02 HR-08/09 例外对齐 / P1-03 权限与回滚语义。
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from ads_core.analysis import assemble_analysis_context, validate_proposals
from ads_core.canonical import build_canonical
from ads_core.decision import (
    DecisionError,
    approve_proposal,
    complete_observation,
    create_action,
    create_observation,
    mark_action_rolled_back,
    record_evaluation,
    record_receipt,
    rollback_action,
    submit_proposals,
)
from ads_core.facts import build_facts
from ads_core.ingest.importer import ingest_file
from ads_core.profile import map_campaign, update_profile, upsert_product
from ads_core.storage import (
    Action,
    ImportBatch,
    MetricDaily,
    Observation,
    Product,
    Proposal,
    session_scope,
)
from ads_core.storage.models import now_iso

from .conftest import make_report, sample_campaign_rows

TODAY = date(2026, 11, 14)
AS_OF = TODAY.isoformat()


def _day(offset: int) -> str:
    return (TODAY - timedelta(days=offset)).isoformat()


def _import_report(env, name: str, rows: list[dict]) -> None:
    """导入 → canonical → facts（campaign+product 粒度）。"""
    f = make_report(Path(env) / name, "campaign", rows)
    with session_scope() as s:
        r = ingest_file(s, f, data_as_of=AS_OF)
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


def _rows_for(campaign_name: str, campaign_id: str, dates: list[str]) -> list[dict]:
    rows = sample_campaign_rows(dates, campaign_id=campaign_id)
    for r in rows:
        r["Campaign Name"] = campaign_name
    return rows


def _make_context_and_proposal(sku: str, date_from: str, date_to: str,
                               fact_id: str, target: str) -> str:
    """上下文 + 通过校验的建议迁入提案 → proposal_id。"""
    with session_scope() as s:
        run = assemble_analysis_context(
            s, sku=sku, date_from=date_from, date_to=date_to, actor="test",
        )
        s.commit()
        proposal_dict = {
            "sku": sku, "target": target,
            "current_performance": "ACOS 30%", "problem": "高于目标",
            "fact_evidence_ids": [fact_id], "event_evidence_ids": [],
            "reasoning": "成熟期 ACOS 偏高",
            "action": "adjust_bid", "adjustment": "-10%", "target_bid": 0.60,
            "metrics_referenced": ["clicks", "spend", "acos"],
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
    assert len(submitted.created) == 1, submitted.skipped_verdict
    return submitted.created[0]


def _approve_and_execute(proposal_id: str, operated_at: str) -> str:
    with session_scope() as s:
        approve_proposal(s, proposal_id, "approve", decided_by="Gordon", actor="webgate")
        s.commit()
        action, created = create_action(s, proposal_id, executor="Gordon")
        s.commit()
        assert created
        aid = action.id
    with session_scope() as s:
        record_receipt(s, aid, result="success", operator="Gordon", operated_at=operated_at)
        s.commit()
    return aid


def _campaign_fact_rows() -> dict[str, MetricDaily]:
    with session_scope() as s:
        rows = s.scalars(select(MetricDaily).where(MetricDaily.grain == "campaign")).all()
        return {m.id: m for m in rows}


# ================================================================
# P0-01 观察窗口 SKU 数据隔离
# ================================================================

@pytest.fixture()
def sku_env(env):
    """双 SKU + 各自活动 + 同 SKU 双活动 + 未映射活动，混合日期范围报表。"""
    with session_scope() as s:
        for sku, asin in (("6978", "B0TEST0001"), ("6979", "B0TEST0002")):
            p = upsert_product(s, sku=sku, asin=asin, actor="test")
            s.commit()
            update_profile(
                s, p.product_id,
                {"A_basic": {"asin": asin}, "B_operations": {"target_acos": 0.25}},
                reason="v1", changed_by="test",
            )
            s.commit()
        mapping = {
            "A-6978-One": ("6978", "ID-A-6978-One"),
            "A-6978-Two": ("6978", "ID-A-6978-Two"),
            "B-6979-One": ("6979", "ID-B-6979-One"),
        }
        for camp, (sku, cid) in mapping.items():
            product = s.scalars(select(Product).where(Product.sku == sku)).first()
            map_campaign(s, campaign_id=cid, product_id=product.id,
                         mapped_by="test", campaign_name=camp, ad_product_type="sp")
            s.commit()

    # 日期混合：基线窗（exec-7~exec-1）与观察窗（exec~exec+7 内成熟行）；
    # "U-Unknown" 刻意不注册映射（Campaign 缺 SKU 归属场景）
    all_dates = [_day(20), _day(18), _day(16), _day(13), _day(11), _day(9)]
    rows = []
    for camp, cid in [
        ("A-6978-One", "ID-A-6978-One"), ("A-6978-Two", "ID-A-6978-Two"),
        ("B-6979-One", "ID-B-6979-One"), ("U-Unknown", "ID-U-Unknown"),
    ]:
        rows += _rows_for(camp, cid, all_dates)
    _import_report(env, "mix.csv", rows)
    return env


def _build_sku_observation(sku: str, exec_day: str) -> str:
    """为指定 SKU 走全链到观察窗完结，返回 observation_id。"""
    with session_scope() as s:
        product = s.scalars(select(Product).where(Product.sku == sku)).first()
        fact = s.scalars(
            select(MetricDaily).where(
                MetricDaily.grain == "campaign",
                MetricDaily.product_id == product.id,
                MetricDaily.date == _day(20),
                MetricDaily.clicks >= 20,
            )
        ).first()
        assert fact is not None
        fact_id = fact.id
    proposal_id = _make_context_and_proposal(
        sku, _day(40), TODAY.isoformat(), fact_id, f"C-{sku}",
    )
    aid = _approve_and_execute(proposal_id, exec_day)
    with session_scope() as s:
        obs = create_observation(s, aid, actor="test")
        s.commit()
        obs_id = obs.id
    with session_scope() as s:
        complete_observation(s, obs_id, actor="test")
        s.commit()
    return obs_id


def _load_obs(obs_id: str) -> tuple[dict, dict]:
    with session_scope() as s:
        obs = s.get(Observation, obs_id)
        return json.loads(obs.baseline_json), json.loads(obs.post_data_json)


class TestP001SkuIsolation:
    """P0-01：观察数据必须严格归属对应 SKU。"""

    def test_two_sku_no_cross_contamination(self, sku_env):
        """两个 SKU 各自观察：基线/观察期互不包含对方数据。"""
        exec_day = _day(13)
        obs_6978 = _build_sku_observation("6978", exec_day)
        obs_6979 = _build_sku_observation("6979", exec_day)

        base78, post78 = _load_obs(obs_6978)
        base79, post79 = _load_obs(obs_6979)

        with session_scope() as s:
            p78 = s.scalars(select(Product).where(Product.sku == "6978")).first()
            p79 = s.scalars(select(Product).where(Product.sku == "6979")).first()
            rows78 = {m.id for m in s.scalars(
                select(MetricDaily).where(MetricDaily.product_id == p78.id)).all()}
            rows79 = {m.id for m in s.scalars(
                select(MetricDaily).where(MetricDaily.product_id == p79.id)).all()}

        assert base78["product_id"] == p78.id and post78["product_id"] == p78.id
        assert base79["product_id"] == p79.id and post79["product_id"] == p79.id
        assert base78["sku"] == post78["sku"] == "6978"
        assert base79["sku"] == post79["sku"] == "6979"
        assert {r["fact_id"] for r in base78["rows"]} <= rows78
        assert {r["fact_id"] for r in post78["rows"]} <= rows78
        assert {r["fact_id"] for r in base79["rows"]} <= rows79
        assert {r["fact_id"] for r in post79["rows"]} <= rows79
        # 验收标准：6978 不含 6979 数据，反之亦然
        assert not ({r["fact_id"] for r in base78["rows"]} & rows79)
        assert not ({r["fact_id"] for r in post79["rows"]} & rows78)
        # 基线/观察期确有数据（非空集通过）
        assert base78["rows"] and post78["rows"] and base79["rows"] and post79["rows"]

    def test_same_sku_multiple_campaigns_aggregated(self, sku_env):
        """同一 SKU 的多个 Campaign 全部纳入该 SKU 观察（不丢行）。"""
        obs = _build_sku_observation("6978", _day(13))
        base, _ = _load_obs(obs)
        facts = _campaign_fact_rows()
        camps = {json.loads(facts[r["fact_id"]].grain_keys_json)["campaign_id"]
                 for r in base["rows"]}
        assert camps == {"ID-A-6978-One", "ID-A-6978-Two"}

    def test_unmapped_campaign_flagged_not_included(self, sku_env):
        """Campaign 缺 SKU 归属：不纳入计算，观察窗标注数据不足。"""
        obs = _build_sku_observation("6978", _day(13))
        base, post = _load_obs(obs)
        facts = _campaign_fact_rows()
        for r in base["rows"] + post["rows"]:
            assert json.loads(facts[r["fact_id"]].grain_keys_json)[
                "campaign_id"] != "ID-U-Unknown"
        assert base["sku_attribution"]["attribution_complete"] is False
        assert base["sku_attribution"]["unattributed_campaign_rows"] == 3
        assert post["sku_attribution"]["attribution_complete"] is False
        assert post["sku_attribution"]["unattributed_campaign_rows"] == 3

    def test_baseline_and_post_use_same_filter(self, sku_env):
        """基线与观察期同一 SKU 过滤口径（sku/product_id/filter 一致）。"""
        obs = _build_sku_observation("6978", _day(13))
        base, post = _load_obs(obs)
        assert base["sku"] == post["sku"] == "6978"
        assert base["product_id"] == post["product_id"]
        assert base["sku_attribution"]["filter"] == post["sku_attribution"]["filter"]

    def test_observation_requires_known_sku_product(self, sku_env):
        """提案 SKU 无产品档案 → 拒绝建观察（归属不明不允许静默纳入）。"""
        with session_scope() as s:
            product = s.scalars(select(Product).where(Product.sku == "6978")).first()
            fact = s.scalars(
                select(MetricDaily).where(
                    MetricDaily.grain == "campaign",
                    MetricDaily.product_id == product.id,
                    MetricDaily.date == _day(20),
                )
            ).first()
            fact_id = fact.id
        proposal_id = _make_context_and_proposal(
            "6978", _day(40), TODAY.isoformat(), fact_id, "A-6978-One",
        )
        with session_scope() as s:
            approve_proposal(s, proposal_id, "approve", decided_by="Gordon")
            s.commit()
            action, _ = create_action(s, proposal_id, executor="Gordon")
            s.commit()
            aid = action.id
        with session_scope() as s:
            record_receipt(s, aid, result="success", operator="Gordon",
                           operated_at=_day(13))
            s.commit()
            # 抹掉 SKU→产品对应（改 sku，模拟归属丢失；直接删除会撞映射外键）
            p = s.scalars(select(Product).where(Product.sku == "6978")).first()
            p.sku = "9999-deleted"
            s.commit()
        with pytest.raises(DecisionError, match="无法确定观察数据归属"):
            with session_scope() as s:
                create_observation(s, aid, actor="test")
                s.commit()


# ================================================================
# P0-02 同期动作识别
# ================================================================

def _insert_raw_action(session, proposal_id: str, approval_id: str, suffix: str,
                       status: str, executed_at: str | None) -> str:
    a = Action(
        proposal_id=proposal_id, approval_id=approval_id, action_type="adjust_bid",
        execute_mode="simulated", status=status, approved_by="G", executor="x",
        executed_at=executed_at, verify_status="unverified",
        idempotency_key=f"test-p002:{suffix}", created_at=now_iso(),
    )
    session.add(a)
    session.flush()
    return a.id


class TestP002ConcurrentActions:
    """P0-02：同期动作只识别观察窗内真实执行的动作。"""

    @pytest.fixture()
    def observed(self, approved_proposal):
        """观察动作（执行日 = _day(13)）→ (action_id, proposal_id, approval_id)。"""
        proposal_id, approval_id = approved_proposal
        with session_scope() as s:
            action, _ = create_action(s, proposal_id, executor="Gordon")
            s.commit()
            aid = action.id
        with session_scope() as s:
            record_receipt(s, aid, result="success", operator="Gordon",
                           operated_at=_day(13))
            s.commit()
        return aid, proposal_id, approval_id

    def _make_observation(self, aid: str) -> dict:
        with session_scope() as s:
            obs = create_observation(s, aid, actor="test")
            s.commit()
            return {
                "id": obs.id,
                "window": [obs.window_start, obs.window_end],
                "concurrent": json.loads(obs.concurrent_actions_json or "[]"),
            }

    def test_window_boundaries(self, observed):
        """窗口前/首日/中间/末日/次日/历史的边界矩阵。"""
        aid, proposal_id, approval_id = observed
        start = date.fromisoformat(_day(13))
        end = start + timedelta(days=14)
        cases = {
            "before-1d": (_day(14), "executed", False),   # 窗口开始前一天
            "at-start": (_day(13), "executed", True),     # 窗口开始当天（含）
            "mid": (_day(6), "executed", True),           # 窗口中间
            "at-end": (end.isoformat(), "executed", True),  # 窗口结束当天（含）
            "after-1d": ((end + timedelta(days=1)).isoformat(), "executed", False),
            "old-40d": (_day(53), "executed", False),     # 历史动作
        }
        with session_scope() as s:
            inserted = {
                name: _insert_raw_action(s, proposal_id, approval_id, name,
                                         status, executed_at)
                for name, (executed_at, status, _) in cases.items()
            }
            s.commit()
        obs = self._make_observation(aid)
        for name, (_, _, expected) in cases.items():
            is_in = inserted[name] in obs["concurrent"]
            assert is_in == expected, (
                f"{name}: executed_at={cases[name][0]} 期望"
                f" {'计入' if expected else '不计入'}，实际 {'计入' if is_in else '不计入'}"
            )

    def test_status_gate(self, observed):
        """状态闸门：未执行状态不计入；rolled_back 曾真实执行计入。"""
        aid, proposal_id, approval_id = observed
        with session_scope() as s:
            inserted = {
                "pending-no-exec": _insert_raw_action(
                    s, proposal_id, approval_id, "pending-no-exec", "pending", None),
                "pending-in-window": _insert_raw_action(
                    s, proposal_id, approval_id, "pending-in-window", "pending", _day(10)),
                "executing": _insert_raw_action(
                    s, proposal_id, approval_id, "executing", "executing", _day(10)),
                "failed": _insert_raw_action(
                    s, proposal_id, approval_id, "failed", "execution_failed", _day(10)),
                "cancelled": _insert_raw_action(
                    s, proposal_id, approval_id, "cancelled", "cancelled", _day(10)),
                "rolled-back": _insert_raw_action(
                    s, proposal_id, approval_id, "rolled-back", "rolled_back", _day(10)),
                "bad-date": _insert_raw_action(
                    s, proposal_id, approval_id, "bad-date", "executed", "not-a-date"),
            }
            s.commit()
        obs = self._make_observation(aid)
        assert inserted["pending-no-exec"] not in obs["concurrent"]
        assert inserted["pending-in-window"] not in obs["concurrent"]
        assert inserted["executing"] not in obs["concurrent"]
        assert inserted["failed"] not in obs["concurrent"]
        assert inserted["cancelled"] not in obs["concurrent"]
        assert inserted["rolled-back"] in obs["concurrent"]
        assert inserted["bad-date"] not in obs["concurrent"]

    def test_self_excluded(self, observed):
        """被观察动作自身不出现在同期动作里。"""
        aid, _, _ = observed
        obs = self._make_observation(aid)
        assert aid not in obs["concurrent"]


# ================================================================
# P1-01 指标引用判断 + P1-02 HR-08/09 例外
# ================================================================

@pytest.fixture()
def validator_env(env):
    """单 SKU：成熟行（clicks=41）+ 未成熟行，映射+事实+分析上下文。"""
    with session_scope() as s:
        p = upsert_product(s, sku="6978", asin="B0TEST0001", actor="test")
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
    rows = sample_campaign_rows([_day(10), _day(2)], campaign_id="111111111111")
    for r in rows:
        r["Campaign Name"] = "TESTNGT-AZDAuto"
        r["Clicks"] = "41"
    _import_report(env, "v_camp.csv", rows)
    with session_scope() as s:
        run = assemble_analysis_context(
            s, sku="6978", date_from=_day(30), date_to=TODAY.isoformat(), actor="test",
        )
        s.commit()
    return {"run_id": run.run_id}


def _mature_fact() -> str:
    with session_scope() as s:
        m = s.scalars(select(MetricDaily).where(
            MetricDaily.grain == "campaign", MetricDaily.date == _day(10),
        )).first()
        assert m is not None
        return m.id


def _immature_fact() -> str:
    with session_scope() as s:
        m = s.scalars(select(MetricDaily).where(
            MetricDaily.grain == "campaign", MetricDaily.date == _day(2),
        )).first()
        assert m is not None and m.maturity != "mature"
        return m.id


def _proposal(**overrides) -> dict:
    p = {
        "sku": "6978", "target": "TESTNGT-AZDAuto",
        "current_performance": "ACOS 30%，点击 41", "problem": "ACOS 高于目标 25%",
        "fact_evidence_ids": [_mature_fact()], "event_evidence_ids": [],
        "reasoning": "成熟期数据显示 ACOS 偏高",
        "action": "adjust_bid", "adjustment": "-10%", "target_bid": 0.60,
        "metrics_referenced": ["clicks", "spend", "orders", "sales", "acos"],
        "expected_effect": "ACOS 向 25% 收敛", "risks": "无",
        "observation_window": "7 天", "acceptance_criteria": "ACOS ≤ 25%",
        "needs_approval": True,
    }
    p.update(overrides)
    return p


def _validate(validator_env, proposals: list[dict]):
    with session_scope() as s:
        verdicts = validate_proposals(s, validator_env["run_id"], proposals, actor="test")
        s.commit()
    return verdicts


class TestP101MetricReference:
    """P1-01：校验器按显式指标引用判断，不再依赖自然语言字段。"""

    def test_immature_volume_only_metrics_pass(self, validator_env):
        """未成熟数据仅引用点击/花费 → 不误判（可通过）。"""
        v = _validate(validator_env, [_proposal(
            fact_evidence_ids=[_immature_fact()],
            metrics_referenced=["clicks", "spend"],
        )])
        assert v[0].verdict == "passed", v[0].violations

    def test_immature_conversion_metrics_rejected(self, validator_env):
        """未成熟数据引用订单/销售额 → VR-01 精准拦截。"""
        v = _validate(validator_env, [_proposal(
            fact_evidence_ids=[_immature_fact()],
            metrics_referenced=["orders", "sales"],
        )])
        assert v[0].verdict == "rejected_hard_rule"
        assert any(x["code"] == "VR-01" for x in v[0].violations)

    def test_immature_reasoning_text_ignored(self, validator_env):
        """reasoning 等自然语言非空不再触发 VR-01（旧缺陷回归）。"""
        v = _validate(validator_env, [_proposal(
            fact_evidence_ids=[_immature_fact()],
            metrics_referenced=["clicks", "spend", "cpc"],
            current_performance="点击 41，花费 10.50（仅体量侧）",
            reasoning="仅基于点击与花费做结构判断",
            problem="点击结构变化",
            expected_effect="维持曝光",
        )])
        assert v[0].verdict == "passed", v[0].violations

    def test_mature_full_metrics_pass(self, validator_env):
        """成熟数据引用完整指标 → 通过。"""
        v = _validate(validator_env, [_proposal(
            metrics_referenced=["impressions", "clicks", "spend", "orders", "sales",
                                "conversions", "acos", "cpc", "cvr"],
        )])
        assert v[0].verdict == "passed", v[0].violations

    def test_missing_reference_safe_default(self, validator_env):
        """缺失 metrics_referenced → 安全原则拦截（不绕过成熟度校验）。"""
        p = _proposal(fact_evidence_ids=[_immature_fact()])
        p.pop("metrics_referenced")
        v = _validate(validator_env, [p])
        assert v[0].verdict == "rejected_hard_rule"
        vr01 = [x for x in v[0].violations if x["code"] == "VR-01"]
        assert vr01 and "未提供 metrics_referenced" in vr01[0]["detail"]

    def test_alias_and_case_normalized(self, validator_env):
        """别名/大小写归一化：ACOS → acos（转化侧）。"""
        v = _validate(validator_env, [_proposal(
            fact_evidence_ids=[_immature_fact()],
            metrics_referenced=["ACOS", "Clicks"],
        )])
        assert v[0].verdict == "rejected_hard_rule"
        assert any(x["code"] == "VR-01" for x in v[0].violations)

    def test_malformed_reference_rejected(self, validator_env):
        """格式错误/未知指标名 → 不绕过，明确返回原因。"""
        for bad in (["unknown_metric"], "orders", [], [123], ["", "clicks"]):
            v = _validate(validator_env, [_proposal(
                fact_evidence_ids=[_immature_fact()], metrics_referenced=bad,
            )])
            assert v[0].verdict == "rejected_hard_rule", f"bad={bad!r}"
            vr01 = [x for x in v[0].violations if x["code"] == "VR-01"]
            assert vr01 and "不可核验" in vr01[0]["detail"], f"bad={bad!r}"

    def test_reference_absent_mature_row_still_passes(self, validator_env):
        """成熟证据行 + 缺失引用声明 → 不触发 VR-01（成熟行无此门槛）。"""
        p = _proposal()
        p.pop("metrics_referenced")
        v = _validate(validator_env, [p])
        assert v[0].verdict == "passed", v[0].violations


class TestP101BidFields:
    """P1-01：出价三值拆分，10% 不再误判为 £10。"""

    def test_adjustment_10pct_not_bid(self, validator_env):
        """adjustment='10%' 不再被识别成目标出价 £10。"""
        v = _validate(validator_env, [_proposal(
            adjustment="10%", metrics_referenced=["clicks"],
        )])
        assert not any(x["code"] == "VR-03" for x in v[0].violations), v[0].violations
        assert v[0].verdict == "passed", v[0].violations

    def test_adjustment_pct_field(self, validator_env):
        """adjustment_pct=10 → 幅度校验通过（<15%）。"""
        v = _validate(validator_env, [_proposal(
            adjustment="维持", adjustment_pct=10, metrics_referenced=["clicks"],
        )])
        assert not any(x["code"] == "VR-02" for x in v[0].violations), v[0].violations
        assert v[0].verdict == "passed", v[0].violations

    def test_target_bid_floor_boundary(self, validator_env):
        """target_bid=0.10 → 边界值在内，通过。"""
        v = _validate(validator_env, [_proposal(target_bid=0.10)])
        assert not any(x["code"] == "VR-03" for x in v[0].violations), v[0].violations
        assert v[0].verdict == "passed", v[0].violations

    def test_target_bid_one_pound(self, validator_env):
        """target_bid=1.00 → 通过。"""
        v = _validate(validator_env, [_proposal(target_bid=1.00)])
        assert v[0].verdict == "passed", v[0].violations

    def test_target_bid_out_of_bounds(self, validator_env):
        """target_bid=3.00 → VR-03 拦截。"""
        v = _validate(validator_env, [_proposal(target_bid=3.00)])
        assert any(x["code"] == "VR-03" for x in v[0].violations)

    def test_bool_not_treated_as_bid(self, validator_env):
        """target_bid=True（bool）不当作出价 1。"""
        v = _validate(validator_env, [_proposal(target_bid=True)])
        assert not any(x["code"] == "VR-03" for x in v[0].violations), v[0].violations

    def test_legacy_new_bid_still_accepted(self, validator_env):
        """历史建议字段 new_bid 兼容（0.60 → 通过）。"""
        v = _validate(validator_env, [_proposal(target_bid=None, new_bid=0.60)])
        assert v[0].verdict == "passed", v[0].violations

    def test_no_bid_extraction_from_string(self, validator_env):
        """adjustment='£1.50' 不从自然语言提取目标出价（VR-03 不判定）。"""
        v = _validate(validator_env, [_proposal(
            adjustment="提到 £1.50", metrics_referenced=["clicks"],
        )])
        assert not any(x["code"] == "VR-03" for x in v[0].violations), v[0].violations


class TestP102HrExceptions:
    """P1-02：HR-08/09 与 YAML 语义一致（例外须写明理由/回滚）。"""

    def test_hr08_over_limit_without_exception_rejected(self, validator_env):
        """-20% 无例外说明 → 拒绝，且提示例外条件。"""
        v = _validate(validator_env, [_proposal(adjustment="-20%")])
        vr02 = [x for x in v[0].violations if x["code"] == "VR-02"]
        assert v[0].verdict == "rejected_hard_rule"
        assert vr02 and "exception_justification" in vr02[0]["detail"]
        assert "exception_rollback_plan" in vr02[0]["detail"]

    def test_hr08_with_justification_and_rollback_passes(self, validator_env):
        """-20% + 理由 + 回滚方案 → HR-08 例外成立（进入待批由人工裁决）。"""
        v = _validate(validator_env, [_proposal(
            adjustment="-20%",
            exception_justification="断崖式流量下滑，需一次性恢复竞争力",
            exception_rollback_plan="若 3 天内 ACOS>50% 则回滚至 0.78",
        )])
        assert v[0].verdict == "passed", v[0].violations

    def test_hr08_justification_without_rollback_rejected(self, validator_env):
        """-20% 只有理由、无回滚方案 → 例外不成立（HR-08 要求理由+回滚）。"""
        v = _validate(validator_env, [_proposal(
            adjustment="-20%",
            exception_justification="断崖式流量下滑",
        )])
        assert v[0].verdict == "rejected_hard_rule"
        assert any(x["code"] == "VR-02" for x in v[0].violations)

    def test_hr09_out_of_bounds_with_justification_passes(self, validator_env):
        """出价 £3.00 + 理由 → HR-09 例外成立。"""
        v = _validate(validator_env, [_proposal(
            target_bid=3.00,
            exception_justification="该词为高转化核心词，参照竞品出价",
        )])
        assert v[0].verdict == "passed", v[0].violations

    def test_hr09_out_of_bounds_without_justification_rejected(self, validator_env):
        """出价 £3.00 无理由 → 拒绝，提示例外条件。"""
        v = _validate(validator_env, [_proposal(target_bid=3.00)])
        vr03 = [x for x in v[0].violations if x["code"] == "VR-03"]
        assert v[0].verdict == "rejected_hard_rule"
        assert vr03 and "exception_justification" in vr03[0]["detail"]

    def test_exception_fields_recorded_in_payload(self, validator_env):
        """例外说明字段保留在建议 payload 中（审批时可追溯）。"""
        v = _validate(validator_env, [_proposal(
            adjustment="-20%",
            exception_justification="理由 X",
            exception_rollback_plan="回滚 Y",
        )])
        assert v[0].verdict == "passed"
        from ads_core.storage import AnalysisRun

        with session_scope() as s:
            run = s.get(AnalysisRun, validator_env["run_id"])
            stored = json.loads(run.proposals_json)[0]["proposal"]
        assert stored["exception_justification"] == "理由 X"
        assert stored["exception_rollback_plan"] == "回滚 Y"


# ================================================================
# P1-03 权限与回滚语义
# ================================================================

class TestP103PermissionsAndRollback:
    """P1-03：审批身份标记 + 记录层回滚语义 + 权限边界。"""

    def test_channel_and_identity_by_actor(self, approved_proposal):
        """channel/identity_verified 按 actor 正确落库（webgate=已验证，其余未验证）。"""
        from ads_core.storage import Approval

        proposal_id, _ = approved_proposal
        with session_scope() as s:
            # 夹具产物已是 approved：每次审批前重置为待批
            s.get(Proposal, proposal_id).status = "pending_approval"
            s.commit()
            approve_proposal(s, proposal_id, "approve", decided_by="Gordon",
                             actor="webgate")
            s.commit()
            s.get(Proposal, proposal_id).status = "pending_approval"
            s.commit()
            approve_proposal(s, proposal_id, "defer", decided_by="Gordon",
                             actor="mcp:proposal_approve:data")
            s.commit()
            s.get(Proposal, proposal_id).status = "pending_approval"
            s.commit()
            approve_proposal(s, proposal_id, "approve", decided_by="Gordon", actor="cli")
            s.commit()
        with session_scope() as s:
            approvals = s.scalars(
                select(Approval).where(Approval.proposal_id == proposal_id)
            ).all()
        # 夹具自身产生一条 cli 审批，故按存在性断言三类记录
        pairs = Counter((a.approval_channel, a.identity_verified) for a in approvals)
        assert pairs[("webgate", 1)] == 1, "webgate 审批应标记已验证"
        assert pairs[("mcp", 0)] == 1, "MCP 审批应标记未验证"
        assert pairs[("cli", 0)] >= 1, "CLI 审批应标记未验证"

    def test_mcp_approve_marked_unverified(self, approved_proposal):
        """MCP 审批：identity_verified=false 且响应说明未验证。"""
        from ads_core.mcp_server import build_server
        from .test_stage5_mcp import _call, _result_text

        proposal_id, _ = approved_proposal
        with session_scope() as s:  # 重置为待批
            s.get(Proposal, proposal_id).status = "pending_approval"
            s.commit()
        server = build_server(["read", "analysis", "data"])
        result = _call(server, "proposal_approve", {
            "proposal_id": proposal_id, "decision": "approve", "decided_by": "Gordon",
        })
        payload = json.loads(_result_text(result))
        assert payload["identity_verified"] is False
        assert payload["channel"] == "mcp"
        assert "未经身份验证" in payload["note"]

    def test_rollback_is_record_layer_only(self, approved_proposal):
        """记录层回滚：状态推进 + scope 标记 + 明确未做平台恢复。"""
        proposal_id, _ = approved_proposal
        with session_scope() as s:
            action, _ = create_action(s, proposal_id, executor="Gordon")
            s.commit()
            aid = action.id
        with session_scope() as s:
            record_receipt(s, aid, result="success", operator="Gordon")
            s.commit()
        with session_scope() as s:
            a = mark_action_rolled_back(s, aid, reason="ACOS 反弹", by="Gordon")
            s.commit()
            result = json.loads(a.result_json)
            assert a.status == "rolled_back"
            assert result["rollback_scope"] == "record_layer_only"
            assert result["external_platform_restored"] is False
        # 兼容别名等价（旧调用面无需改动）
        assert rollback_action is mark_action_rolled_back

    def test_mcp_rollback_response_flags(self, approved_proposal):
        """MCP 回滚响应携带 scope 与平台恢复标记。"""
        from ads_core.mcp_server import build_server
        from .test_stage5_mcp import _call, _result_text

        proposal_id, _ = approved_proposal
        with session_scope() as s:
            action, _ = create_action(s, proposal_id, executor="Gordon")
            s.commit()
            aid = action.id
            record_receipt(s, aid, result="success", operator="Gordon")
            s.commit()
        server = build_server(["read", "analysis", "data"])
        result = _call(server, "action_rollback",
                       {"action_id": aid, "reason": "反弹", "by": "Gordon"})
        payload = json.loads(_result_text(result))
        assert payload["rollback_scope"] == "record_layer_only"
        assert payload["external_platform_restored"] is False

    def test_analysis_agent_cannot_reach_write_tools(self, env):
        """普通分析 Agent（read+analysis）物理拿不到审批/执行类工具。"""
        from ads_core.mcp_server import list_tools

        tools = list_tools(["read", "analysis"])
        for forbidden in ("proposal_approve", "proposal_submit", "action_create",
                          "action_receipt", "action_rollback", "observation_create",
                          "evaluation_record", "ingest_file"):
            assert forbidden not in tools, f"analysis 级不应注册 {forbidden}"
        # data 级才可见（本地受限操作面）
        data_tools = list_tools(["read", "analysis", "data"])
        assert {"proposal_approve", "action_create", "action_receipt",
                "action_rollback"} <= set(data_tools)

    def test_evaluation_g6_still_guarded(self, approved_proposal):
        """回归：观察未完结不可验收（P0-02 修复不弱化 G6 守卫）。"""
        proposal_id, _ = approved_proposal
        with session_scope() as s:
            action, _ = create_action(s, proposal_id, executor="Gordon")
            s.commit()
            aid = action.id
            record_receipt(s, aid, result="success", operator="Gordon",
                           operated_at=_day(13))
            s.commit()
        with session_scope() as s:
            create_observation(s, aid, actor="test")
            s.commit()
        with pytest.raises(DecisionError, match="观察未完结"):
            with session_scope() as s:
                record_evaluation(
                    s, aid, verdict="valid",
                    attribution={"what_changed": [], "possibly_related": [],
                                 "strongly_supported": [], "other_factors": []},
                    decided_by="Gordon",
                )
                s.commit()
