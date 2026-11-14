# -*- coding: utf-8 -*-
"""MCP Server（阶段五 · D1：service 层薄适配，零业务逻辑）。

权限分级（物理不注册 = 最硬边界）：
  read（默认）  只读查询
  analysis      + 上下文组装 / 建议校验 / 经验写入
  data          + 数据导入与决策链写操作（高危，仅受信本机）

每次调用统一写 audit_log（actor=mcp:<tool>:<perms>；密钥/二进制内容不入日志）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from fastmcp import FastMCP

from . import config
from .storage import reset_engine, session_scope
from .storage.audit import write_audit

PERMISSION_LEVELS = ("read", "analysis", "data")


def build_server(permissions: list[str] | None = None) -> FastMCP:
    """按权限级别构建 FastMCP 实例（未列级别的工具不注册）。"""
    granted = {p for p in (permissions or ["read"]) if p in PERMISSION_LEVELS}
    if not granted:
        raise ValueError(f"permissions 至少含其一：{PERMISSION_LEVELS}")
    mcp: FastMCP = FastMCP(name="ads", instructions=(
        "亚马逊广告智能优化与决策系统。核心规则：① 事实与判断分离——上下文包由引擎确定性组装，"
        "建议须过三关校验；② 未成熟数据（HR-01/02）不得作为正式结论依据；"
        "③ 写操作仅 data 权限可用。"
    ))

    def _audit(tool: str, args: dict) -> None:
        """调用留痕（参数摘要，密钥/大对象不入日志）。"""
        with session_scope() as s:
            write_audit(
                s, f"mcp:{tool}:{'+'.join(sorted(granted))}", "mcp.call", "tool", tool,
                {k: (str(v)[:80]) for k, v in args.items()},
            )
            s.commit()

    # ---------------------------------------------------------------- read

    if "read" in granted:
        @mcp.tool
        def methodology_show(latest: bool = True) -> str:
            """查看已发布方法论版本（默认最新）。"""
            from .methodology import latest_released
            from .storage import MethodologyVersion
            from sqlalchemy import select as _sel

            _audit("methodology_show", {"latest": latest})
            with session_scope() as s:
                if latest:
                    v = latest_released(s)
                    rows = [v] if v else []
                else:
                    rows = list(s.scalars(_sel(MethodologyVersion).order_by(MethodologyVersion.created_at.desc())).all())
                return json.dumps([
                    {"version_no": v.version_no, "status": v.status, "title": v.title,
                     "released_at": v.released_at} for v in rows
                ], ensure_ascii=False)

        @mcp.tool
        def facts_maturity(date_from: Optional[str] = None, date_to: Optional[str] = None) -> str:
            """事实指标成熟度统计（未成熟占比，QF-10 视图）。"""
            from .storage import MetricDaily
            from sqlalchemy import select as _sel

            _audit("facts_maturity", {"date_from": date_from, "date_to": date_to})
            with session_scope() as s:
                stmt = _sel(MetricDaily)
                if date_from:
                    stmt = stmt.where(MetricDaily.date >= date_from)
                if date_to:
                    stmt = stmt.where(MetricDaily.date <= date_to)
                rows = s.scalars(stmt).all()
            total = len(rows)
            immature = sum(1 for r in rows if r.maturity == "immature_observation")
            return json.dumps({
                "total": total, "immature": immature,
                "immature_ratio": round(immature / total, 4) if total else None,
            }, ensure_ascii=False)

        @mcp.tool
        def batch_list(status: Optional[str] = None) -> str:
            """导入批次列表。"""
            from .storage import ImportBatch
            from sqlalchemy import select as _sel

            _audit("batch_list", {"status": status})
            with session_scope() as s:
                stmt = _sel(ImportBatch).order_by(ImportBatch.created_at.desc())
                if status:
                    stmt = stmt.where(ImportBatch.status == status)
                rows = s.scalars(stmt).all()
                return json.dumps([
                    {"id": b.id, "status": b.status, "source_system": b.source_system,
                     "imported_at": b.imported_at} for b in rows
                ], ensure_ascii=False)

        @mcp.tool
        def batch_show(batch_id: str) -> str:
            """批次详情（含 raw 文件与质检发现）。"""
            from .storage import ImportBatch

            _audit("batch_show", {"batch_id": batch_id})
            with session_scope() as s:
                b = s.get(ImportBatch, batch_id)
                if b is None:
                    return json.dumps({"error": "批次不存在"}, ensure_ascii=False)
                from .storage import QualityFinding, RawDataset
                from sqlalchemy import select as _sel

                raws = s.scalars(_sel(RawDataset).where(RawDataset.batch_id == batch_id)).all()
                findings = s.scalars(_sel(QualityFinding).where(QualityFinding.batch_id == batch_id)).all()
                return json.dumps({
                    "id": b.id, "status": b.status,
                    "raw_datasets": [
                        {"file_name": r.file_name, "report_type": r.report_type,
                         "status": r.status, "rows": r.row_count} for r in raws
                    ],
                    "findings": [
                        {"code": f.check_code, "severity": f.severity, "message": f.message}
                        for f in findings
                    ],
                }, ensure_ascii=False)

        @mcp.tool
        def analysis_show(run_id: str) -> str:
            """分析运行详情（含上下文 flags 与建议校验结果）。"""
            from .storage import AnalysisRun

            _audit("analysis_show", {"run_id": run_id})
            with session_scope() as s:
                run = s.get(AnalysisRun, run_id)
                if run is None:
                    return json.dumps({"error": "运行不存在"}, ensure_ascii=False)
                return json.dumps({
                    "run_id": run.id, "sku": run.sku, "status": run.status,
                    "date_range": [run.date_from, run.date_to],
                    "flags": json.loads(run.context_flags_json or "{}"),
                }, ensure_ascii=False)

        @mcp.tool
        def proposal_list(status: Optional[str] = None) -> str:
            """提案列表。"""
            from .storage import Proposal
            from sqlalchemy import select as _sel

            _audit("proposal_list", {"status": status})
            with session_scope() as s:
                stmt = _sel(Proposal).order_by(Proposal.created_at.desc())
                if status:
                    stmt = stmt.where(Proposal.status == status)
                rows = s.scalars(stmt).all()
                return json.dumps([
                    {"id": p.id, "sku": p.sku, "target": p.target,
                     "action_type": p.action_type, "status": p.status} for p in rows
                ], ensure_ascii=False)

        @mcp.tool
        def action_list(status: Optional[str] = None) -> str:
            """广告动作列表。"""
            from .storage import Action
            from sqlalchemy import select as _sel

            _audit("action_list", {"status": status})
            with session_scope() as s:
                stmt = _sel(Action).order_by(Action.created_at.desc())
                if status:
                    stmt = stmt.where(Action.status == status)
                rows = s.scalars(stmt).all()
                return json.dumps([
                    {"id": a.id, "status": a.status, "mode": a.execute_mode,
                     "verify_status": a.verify_status} for a in rows
                ], ensure_ascii=False)

        @mcp.tool
        def experience_search(query: str, limit: int = 5) -> str:
            """经验库检索（FTS5 中文）。"""
            from .experience import search_experience

            _audit("experience_search", {"query": query})
            with session_scope() as s:
                hits = search_experience(s, query, limit=limit)
                return json.dumps([
                    {"exp_code": h.exp_code, "scenario": h.scenario,
                     "status": h.status} for h in hits
                ], ensure_ascii=False)

        @mcp.tool
        def evaluation_stats(date_from: Optional[str] = None, date_to: Optional[str] = None) -> str:
            """效果验收统计（verdict 分布 / 有效率 / 证据不足占比）。"""
            from .decision.stats import evaluation_stats as _stats

            _audit("evaluation_stats", {"date_from": date_from, "date_to": date_to})
            with session_scope() as s:
                st = _stats(s, date_from, date_to)
                return json.dumps({
                    "total": st.total, "by_verdict": st.by_verdict,
                    "effective_rate": st.effective_rate,
                    "insufficient_ratio": st.insufficient_ratio,
                    "rollback_suggested": st.rollback_suggested,
                }, ensure_ascii=False)

    # ---------------------------------------------------------------- analysis

    if "analysis" in granted:
        @mcp.tool
        def analysis_context(
            sku: str, date_from: str, date_to: str, top_terms: int = 50
        ) -> str:
            """组装分析上下文包（PRD 4.7 七步；成熟度强制隔离 HR-01/02）。"""
            from .analysis import ContextError, assemble_analysis_context

            _audit("analysis_context", {"sku": sku, "date_from": date_from, "date_to": date_to})
            try:
                with session_scope() as s:
                    result = assemble_analysis_context(
                        s, sku=sku, date_from=date_from, date_to=date_to,
                        top_terms=top_terms, actor="mcp:analysis_context",
                    )
                    s.commit()
                return json.dumps({
                    "run_id": result.run_id, "status": result.status,
                    "flags": result.flags,
                    "next": "读包产建议后调 analysis_validate（proposal_schema 见 ads analysis context --dump）",
                }, ensure_ascii=False)
            except ContextError as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)

        @mcp.tool
        def analysis_validate(run_id: str, proposals: list[dict[str, Any]]) -> str:
            """校验 AI 建议（三关：Schema14 → 硬规则 VR-01~06 → 证据存在性 EV-01/02）。"""
            from .analysis import ValidatorError, validate_proposals

            _audit("analysis_validate", {"run_id": run_id, "n_proposals": len(proposals)})
            try:
                with session_scope() as s:
                    verdicts = validate_proposals(
                        s, run_id, proposals, actor="mcp:analysis_validate"
                    )
                    s.commit()
                return json.dumps([
                    {"index": v.index, "verdict": v.verdict, "violations": v.violations}
                    for v in verdicts
                ], ensure_ascii=False)
            except ValidatorError as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)

        @mcp.tool
        def experience_add(payload: dict[str, Any]) -> str:
            """沉淀经验（字段：scenario/original_problem/created_by/...，失败经验必留）。"""
            from .experience import ExperienceError, add_experience

            _audit("experience_add", {"scenario": payload.get("scenario")})
            try:
                with session_scope() as s:
                    exp = add_experience(s, actor="mcp:experience_add", **payload)
                    s.commit()
                return json.dumps({"exp_code": exp.exp_code, "status": exp.status}, ensure_ascii=False)
            except ExperienceError as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)

        @mcp.tool
        def experience_draft(evaluation_id: str, created_by: str) -> str:
            """验收结果一键转经验草稿（Gordon 审核后改 verified/failed）。"""
            from .decision.stats import evaluation_to_experience_draft

            _audit("experience_draft", {"evaluation_id": evaluation_id})
            with session_scope() as s:
                exp = evaluation_to_experience_draft(
                    s, evaluation_id, created_by=created_by, actor="mcp:experience_draft"
                )
                s.commit()
            return json.dumps({"exp_code": exp.exp_code, "status": exp.status}, ensure_ascii=False)

    # ---------------------------------------------------------------- data（高危）

    if "data" in granted:
        @mcp.tool
        def ingest_file(
            file_path: str,
            ad_product_type: str = "sp",
            forced_type: Optional[str] = None,
            imported_by: str = "mcp",
        ) -> str:
            """导入报表文件（file_hash 幂等；写操作）。"""
            from .ingest import ingest_file as _ingest

            _audit("ingest_file", {"file_path": file_path, "ad_product_type": ad_product_type})
            try:
                with session_scope() as s:
                    r = _ingest(
                        s, file_path, imported_by=imported_by,
                        ad_product_type=ad_product_type, forced_type=forced_type,
                    )
                    s.commit()
                return json.dumps({
                    "batch_id": r.batch_id, "duplicate": r.duplicate,
                    "status": r.status, "detected_type": r.detected_type,
                    "rows": r.row_count,
                    "findings": [{"code": f.check_code, "severity": f.severity} for f in r.findings],
                }, ensure_ascii=False)
            except Exception as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)

        @mcp.tool
        def proposal_submit(run_id: str, by: str = "mcp") -> str:
            """校验通过建议迁入提案（写操作）。"""
            from .decision import submit_proposals

            _audit("proposal_submit", {"run_id": run_id})
            with session_scope() as s:
                r = submit_proposals(s, run_id, actor=by)
                s.commit()
            return json.dumps({"created": r.created, "skipped": r.skipped_verdict}, ensure_ascii=False)

        @mcp.tool
        def proposal_approve(
            proposal_id: str, decision: str, decided_by: str,
            modified_payload: Optional[dict] = None, comment: Optional[str] = None,
        ) -> str:
            """人工审批（approve|modify_approve|reject|defer；写操作）。"""
            from .decision import approve_proposal

            _audit("proposal_approve", {"proposal_id": proposal_id, "decision": decision})
            with session_scope() as s:
                approval = approve_proposal(
                    s, proposal_id, decision, decided_by=decided_by,
                    modified_payload=modified_payload, comment=comment,
                )
                s.commit()
            return json.dumps({"approval_id": approval.id}, ensure_ascii=False)

        @mcp.tool
        def action_create(
            proposal_id: str, executor: str,
            execute_mode: str = "simulated",
            rollback_policy: Optional[dict] = None,
        ) -> str:
            """创建广告动作（须已审批；幂等；写操作）。"""
            from .decision import create_action

            _audit("action_create", {"proposal_id": proposal_id, "mode": execute_mode})
            with session_scope() as s:
                action, created = create_action(
                    s, proposal_id, executor=executor, execute_mode=execute_mode,
                    rollback_policy=rollback_policy,
                )
                s.commit()
            return json.dumps({"action_id": action.id, "created": created}, ensure_ascii=False)

        @mcp.tool
        def action_receipt(
            action_id: str, result: str, operator: str,
            operated_at: Optional[str] = None,
        ) -> str:
            """录入执行回执（success|failed|unknown；写操作）。"""
            from .decision import record_receipt

            _audit("action_receipt", {"action_id": action_id, "result": result})
            with session_scope() as s:
                action = record_receipt(
                    s, action_id, result=result, operator=operator, operated_at=operated_at,
                )
                s.commit()
            return json.dumps({"status": action.status, "verify_status": action.verify_status}, ensure_ascii=False)

        @mcp.tool
        def action_rollback(action_id: str, reason: str, by: str) -> str:
            """回滚动作（留痕不删；写操作）。"""
            from .decision import rollback_action

            _audit("action_rollback", {"action_id": action_id})
            with session_scope() as s:
                rollback_action(s, action_id, reason=reason, by=by)
                s.commit()
            return json.dumps({"action_id": action_id, "status": "rolled_back"}, ensure_ascii=False)

        @mcp.tool
        def observation_create(action_id: str) -> str:
            """创建观察窗口（F5；写操作）。"""
            from .decision import create_observation

            _audit("observation_create", {"action_id": action_id})
            with session_scope() as s:
                obs = create_observation(s, action_id)
                s.commit()
            return json.dumps({"observation_id": obs.id, "window": [obs.window_start, obs.window_end]}, ensure_ascii=False)

        @mcp.tool
        def evaluation_record(
            action_id: str, verdict: str, attribution: dict, decided_by: str,
            evidence_strength: str = "medium",
        ) -> str:
            """效果验收（四裁决+归因四层；写操作）。"""
            from .decision import record_evaluation

            _audit("evaluation_record", {"action_id": action_id, "verdict": verdict})
            with session_scope() as s:
                ev = record_evaluation(
                    s, action_id, verdict=verdict, attribution=attribution,
                    decided_by=decided_by, evidence_strength=evidence_strength,
                )
                s.commit()
            return json.dumps({"evaluation_id": ev.id, "verdict": ev.verdict,
                               "rollback_suggested": bool(ev.rollback_suggested)}, ensure_ascii=False)

    return mcp


def serve(permissions: list[str]) -> None:
    """启动 stdio MCP 服务。"""
    reset_engine()
    server = build_server(permissions)
    server.run(transport="stdio")


def list_tools(permissions: list[str]) -> list[str]:
    """本地自检：该权限级别将注册的工具名。"""
    server = build_server(permissions)
    import asyncio

    async def _names() -> list[str]:
        tools = await server.list_tools()  # fastmcp 4：list[FunctionTool]
        return sorted(t.name for t in tools)

    return asyncio.run(_names())
