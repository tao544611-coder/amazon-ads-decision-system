# -*- coding: utf-8 -*-
"""6978/6979 真实历史数据验证（2026-11-14 缺陷修复任务单 · 第三层验证）。

数据来源：领星开放平台 ad_sp_campaign_report（日粒度活动报表，只读 MCP 调用），
原始行与真实标识（sid/profile/ASIN/活动名/回放元数据）全部存档于
data/raw/6978_6979_validation_20261114/（gitignored，仅本机）；本脚本零真实标识。

真实前提（2026-W46 补录 facts + products.md 佐证，详见存档 provenance）：
- 6978（3 ASIN）与 6979（2 ASIN）同类同步投放，全部 7 个活动为两 SKU 共用；
  campaign 粒度无法唯一归属 → 按 P0-01 要求 6，不注册 CampaignMap，验证
  「归属不明数据不静默纳入、必须标记数据不足」。
- 真实历史动作回放（台账记录，本地记录层模拟，不触后台）：
  SB01（2026-10-23 领星手建 SB 活动）、EXEC01（2026-11-09 领星执行三条减支）。
- 真实业务事件：促销退出（2026-11-10 起，W46 补录专项）。

预期（修复后行为）：
- 基线窗 [2026-11-02, 2026-11-09) 成熟行全部因归属不明被排除并计数标注；
- 观察窗 [2026-11-09, 2026-11-23] 成熟行 0（11-09 行按 HR-01 未成熟）；
- SB01（10-23，窗外）不计入同期动作；EXEC01（11-09，窗首日）计入同期动作；
- 促销退出事件计入同期事件（T7）；
- MetricDaily 与原始存档逐行一致（关键指标一致，任务单要求 7）；
- 存在同期干扰时 G6 守卫生效。

用法：cd ads_core && .venv/Scripts/python -X utf8 ../scripts/validate_real_6978_6979.py
"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ARCHIVE = REPO / "data" / "raw" / "6978_6979_validation_20261114" / \
    "lingxing_ad_sp_campaign_report_daily_2026-11-02_2026-11-09.json"
sys.path.insert(0, str(REPO / "ads_core"))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from ads_core import config  # noqa: E402
from ads_core.analysis import assemble_analysis_context  # noqa: E402
from ads_core.canonical import build_canonical  # noqa: E402
from ads_core.decision import (  # noqa: E402
    DecisionError,
    approve_proposal,
    complete_observation,
    create_action,
    create_observation,
    mark_action_rolled_back,
    record_evaluation,
    record_receipt,
)
from ads_core.events import add_business_event  # noqa: E402
from ads_core.facts import build_facts  # noqa: E402
from ads_core.ingest.importer import ingest_file  # noqa: E402
from ads_core.methodology import methodology_import  # noqa: E402
from ads_core.profile import update_profile, upsert_product  # noqa: E402
from ads_core.storage import (  # noqa: E402
    AnalysisRun,
    ImportBatch,
    MetricDaily,
    MethodologyVersion,
    Observation,
    Product,
    ProductProfileVersion,
    Proposal,
    reset_engine,
    session_scope,
)
from ads_core.storage.models import now_iso  # noqa: E402

DATA_AS_OF = "2026-11-14"          # 真实取数日（存档 provenance.fetched_at 日期）

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  | {detail}" if detail else ""))


def main() -> int:
    assert ARCHIVE.is_file(), f"原始存档不存在：{ARCHIVE}（真实数据仅本机，见 README 数据与隐私）"
    archive = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    rows = archive["rows"]
    names = archive["campaign_names"]
    replay = archive["replay"]
    products = archive["products"]

    tmp = Path(tempfile.mkdtemp(prefix="ads_real_6978_"))
    import os

    os.environ["ADS_HOME"] = str(tmp)
    os.environ.pop("ADS_DB", None)
    os.environ["ADS_LINGXING_MCP_RAW"] = str(tmp / "lingxing-missing")
    config.ensure_dirs()
    cfg = Config(str(REPO / "ads_core" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO / "ads_core" / "alembic"))
    command.upgrade(cfg, "head")
    reset_engine()
    with session_scope() as s:
        methodology_import(s, REPO / "methodology" / "v1.0.0.yaml")
        s.commit()

    # ---- 1. 产品+档案（不注册任何 CampaignMap：全部活动为 6978/6979 共用）----
    with session_scope() as s:
        for sku, info in products.items():
            p = upsert_product(s, sku=sku, asin=info["primary_asin"],
                               actor="real-validation")
            s.commit()
            update_profile(s, p.product_id,
                           {"A_basic": {"asin": info["primary_asin"]}},
                           reason="real-validation", changed_by="gordon-replay")
            s.commit()
        p79_id = s.scalars(select(Product).where(Product.sku == "6979")).first().id

    # ---- 2. 存档 → bulk 风格 CSV → 导入 → canonical → facts ----
    csv_path = tmp / "real_sp_campaign_daily.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "Portfolio name", "Campaign Id", "Campaign Name",
                    "Campaign Status", "Impressions", "Clicks", "Spend",
                    "7 Day Total Orders (#)", "7 Day Total Sales"])
        for r in rows:
            w.writerow([r["report_date"], "", r["campaign_id"],
                        names[r["campaign_id"]], "ENABLED",
                        r["impressions"], r["clicks"], f"{r['cost']:.2f}",
                        r["orders"], f"{r['sales']:.2f}"])
    with session_scope() as s:
        r = ingest_file(s, str(csv_path), data_as_of=DATA_AS_OF,
                        ad_product_type="sp", imported_by="real-validation")
        s.commit()
    check("导入批次完成", r.status in ("complete", "needs_confirm"),
          f"status={r.status} rows={r.row_count}")
    if r.status == "needs_confirm":
        with session_scope() as s:
            s.get(ImportBatch, r.batch_id).status = "complete"
            s.commit()
    with session_scope() as s:
        build_canonical(s, r.batch_id, actor="real-validation")
        s.commit()
    with session_scope() as s:
        fr = build_facts(s, grains=["campaign"], actor="real-validation")
        s.commit()
    check("事实行数=存档行数", fr.rows_written == len(rows),
          f"facts={fr.rows_written} archive={len(rows)}")

    # ---- 3. 关键指标一致性：MetricDaily vs 原始存档（逐 campaign×date）----
    mismatch = []
    with session_scope() as s:
        md_rows = s.scalars(select(MetricDaily).where(MetricDaily.grain == "campaign")).all()
        eng = {}
        for m in md_rows:
            keys = json.loads(m.grain_keys_json)
            eng[(keys["campaign_id"], m.date)] = m
        for r0 in rows:
            m = eng.get((r0["campaign_id"], r0["report_date"]))
            if m is None:
                mismatch.append(f"{r0['campaign_id']}/{r0['report_date']} 缺行")
                continue
            for field, val in (("impressions", r0["impressions"]), ("clicks", r0["clicks"]),
                               ("spend", r0["cost"]), ("orders", r0["orders"]),
                               ("sales", r0["sales"])):
                got = getattr(m, field)
                if got is None or abs(float(got) - float(val)) > 1e-6:
                    mismatch.append(
                        f"{r0['campaign_id']}/{r0['report_date']} {field}: 引擎={got} 原始={val}")
        raw_tot = {k: round(sum(r0[k] for r0 in rows), 2) for k in
                   ("cost", "clicks", "impressions", "orders", "sales")}
        eng_tot = {
            "cost": round(sum(float(m.spend or 0) for m in md_rows), 2),
            "clicks": sum(int(m.clicks or 0) for m in md_rows),
            "impressions": sum(int(m.impressions or 0) for m in md_rows),
            "orders": sum(int(m.orders or 0) for m in md_rows),
            "sales": round(sum(float(m.sales or 0) for m in md_rows), 2),
        }
    check("逐行指标一致（引擎 vs 原始存档）", not mismatch, "; ".join(mismatch[:3]))
    check("全窗合计一致", raw_tot == eng_tot, f"原始={raw_tot} 引擎={eng_tot}")

    # ---- 4. 成熟度（HR-01：SP 按点击日回推，取数日=DATA_AS_OF）----
    expect_mature = sum(1 for r0 in rows
                        if (date.fromisoformat(DATA_AS_OF)
                            - date.fromisoformat(r0["report_date"])).days >= 7)
    expect_immature = len(rows) - expect_mature
    with session_scope() as s:
        mature_cnt = s.scalar(select(func.count()).select_from(MetricDaily).where(
            MetricDaily.grain == "campaign", MetricDaily.maturity == "mature"))
        immature_cnt = s.scalar(select(func.count()).select_from(MetricDaily).where(
            MetricDaily.grain == "campaign", MetricDaily.maturity != "mature"))
    check("成熟度符合 HR-01", mature_cnt == expect_mature and immature_cnt == expect_immature,
          f"mature={mature_cnt}/{expect_mature} immature={immature_cnt}/{expect_immature}")

    # ---- 5. 上下文组装（无映射 → 数据缺口如实标注）----
    with session_scope() as s:
        ctx = assemble_analysis_context(
            s, sku="6978", date_from=rows[0]["report_date"], date_to=DATA_AS_OF,
            actor="real-validation")
        s.commit()
    with session_scope() as s:
        run = s.get(AnalysisRun, ctx.run_id)
        flags = json.loads(run.context_flags_json)
    check("上下文标注数据缺口（campaign 粒度无归属行）",
          "campaign 粒度无数据" in " ".join(flags["data_gaps"])
          and flags["needs_manual_attention"] is True,
          f"data_gaps={flags['data_gaps']}")

    # ---- 6. 真实业务事件：促销退出（W46 补录）----
    ev = replay["promo_exit_event"]
    with session_scope() as s:
        add_business_event(s, event_type=ev["event_type"], starts_at=ev["starts_at"],
                           description=ev["description"], recorded_by="gordon-replay",
                           product_id=p79_id if ev["sku"] == "6979" else
                           s.scalars(select(Product).where(
                               Product.sku == ev["sku"])).first().id)
        s.commit()

    # ---- 7. 真实动作回放（记录层模拟；提案为人工回放件，出处见存档 memo）----
    def _replay_action(replay_key: str) -> str:
        spec = replay[replay_key]
        with session_scope() as s:
            mv = s.scalars(select(MethodologyVersion)).first()
            pv = s.scalars(select(ProductProfileVersion).where(
                ProductProfileVersion.product_id == (
                    s.scalars(select(Product).where(
                        Product.sku == spec["sku"])).first().id))
            ).first()
            run = AnalysisRun(
                sku=spec["sku"], date_from=rows[0]["report_date"], date_to=DATA_AS_OF,
                marketplace="UK", status="context_ready",
                methodology_version_id=mv.id, profile_version_id=pv.id,
                batch_ids_json="[]", context_json=json.dumps(
                    {"replay_note": spec["memo"]}, ensure_ascii=False),
                created_by="gordon-replay", created_at=now_iso(),
            )
            s.add(run)
            s.flush()
            prop = Proposal(
                analysis_run_id=run.id, proposal_index=0, sku=spec["sku"],
                target=spec["target_campaign"],
                action_type="pause_and_lower_bid",
                payload_json=json.dumps({"replay_of": spec["memo"]}, ensure_ascii=False),
                methodology_version_id=mv.id, profile_version_id=pv.id,
                status="pending_approval", created_by="gordon-replay",
                created_at=now_iso(),
            )
            s.add(prop)
            s.flush()
            pid = prop.id
            s.commit()
        with session_scope() as s:
            approve_proposal(s, pid, "approve", decided_by="Gordon", actor="webgate")
            s.commit()
            action, created = create_action(s, pid, executor="lingxing-replay",
                                            execute_mode="simulated")
            s.commit()
            aid = action.id
        with session_scope() as s:
            record_receipt(s, aid, result="success", operator="lingxing-replay",
                           operated_at=spec["operated_at"])
            s.commit()
        return aid

    aid_exec01_78 = _replay_action("exec01_6978")
    aid_sb01 = _replay_action("sb01")
    aid_exec01_79 = _replay_action("exec01_6979")

    # ---- 8. 观察窗口：基线/观察期归属不明不纳入、历史动作不干扰 ----
    with session_scope() as s:
        obs78 = create_observation(s, aid_exec01_78, actor="real-validation")
        s.commit()
        obs79 = create_observation(s, aid_exec01_79, actor="real-validation")
        s.commit()
        obs_ids = (obs78.id, obs79.id)
        w78 = [obs78.window_start, obs78.window_end]
        conc78 = json.loads(obs78.concurrent_actions_json or "[]")
        conc79 = json.loads(obs79.concurrent_actions_json or "[]")
        ev78 = json.loads(obs78.concurrent_events_json or "[]")
    check("观察窗 = 执行日起 14 天（F5）",
          w78 == ["2026-11-09", "2026-11-23"], str(w78))
    check("历史动作 SB01（10-23）不干扰当前观察窗",
          aid_sb01 not in conc78, f"concurrent={conc78}")
    check("同日真实动作（6979 侧 11-09）计入同期动作",
          aid_exec01_79 in conc78 and aid_exec01_78 in conc79)
    check("促销退出事件（11-10 起）计入同期事件",
          len(ev78) == 1 and ev78[0]["event_type"] == ev["event_type"], str(ev78))

    for obs_id, sku_tag in zip(obs_ids, ("6978", "6979")):
        with session_scope() as s:
            complete_observation(s, obs_id, actor="real-validation")
            s.commit()
        with session_scope() as s:
            obs = s.get(Observation, obs_id)
            base = json.loads(obs.baseline_json)
            post = json.loads(obs.post_data_json)
        expected_baseline_unattr = expect_mature  # 基线窗全部成熟行均归属不明
        check(f"[{sku_tag}] 基线归属不明行全部排除且计数标注",
              base["rows"] == [] and
              base["sku_attribution"]["unattributed_campaign_rows"] == expected_baseline_unattr
              and base["sku_attribution"]["attribution_complete"] is False,
              f"rows={len(base['rows'])} "
              f"unattributed={base['sku_attribution']['unattributed_campaign_rows']}")
        check(f"[{sku_tag}] 观察期无成熟行（执行日起未成熟，HR-01）且不虚标",
              post["rows"] == [] and post["sku_attribution"]["attribution_complete"] is True,
              f"rows={len(post['rows'])}")

    # ---- 9. G6：存在同期干扰时不得强行归因 ----
    blocked = False
    try:
        with session_scope() as s:
            record_evaluation(
                s, aid_exec01_78, verdict="valid",
                attribution={"what_changed": ["ACOS 下降"], "possibly_related": ["降价"],
                             "strongly_supported": [], "other_factors": []},
                decided_by="Gordon")
            s.commit()
    except DecisionError:
        blocked = True
    check("G6：同期干扰存在时强行归因被拒", blocked)

    # ---- 10. 记录层回滚语义（回放件标注，不触后台）----
    with session_scope() as s:
        a = mark_action_rolled_back(s, aid_sb01, reason="验证脚本：记录层回滚演示",
                                    by="gordon-replay")
        s.commit()
        rj = json.loads(a.result_json)
    check("记录层回滚标记 scope=record_layer_only 且未虚称平台恢复",
          a.status == "rolled_back" and rj["rollback_scope"] == "record_layer_only"
          and rj["external_platform_restored"] is False)

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n===== 真实数据验证汇总：{len(results) - len(failed)}/{len(results)} 通过 =====")
    if failed:
        print("未通过项：", failed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
