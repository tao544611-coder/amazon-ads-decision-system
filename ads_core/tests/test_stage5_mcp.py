# -*- coding: utf-8 -*-
"""阶段五测试：MCP Server 权限分级 / 工具冒烟 / 调用留痕。"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import select

from ads_core.mcp_server import build_server, list_tools
from ads_core.storage import AuditLog, session_scope


def test_mcp_permission_read_level(env):
    """read 级：只读工具注册，无任何写工具。"""
    tools = list_tools(["read"])
    assert {"methodology_show", "facts_maturity", "batch_list", "experience_search"} <= set(tools)
    for forbidden in ("ingest_file", "analysis_context", "analysis_validate",
                      "proposal_approve", "action_create", "experience_add"):
        assert forbidden not in tools, f"read 级不应注册 {forbidden}"


def test_mcp_permission_analysis_level(env):
    """analysis 级：+分析工具，仍无数据写工具。"""
    tools = list_tools(["read", "analysis"])
    assert {"analysis_context", "analysis_validate", "experience_add"} <= set(tools)
    for forbidden in ("ingest_file", "proposal_approve", "action_create", "evaluation_record"):
        assert forbidden not in tools


def test_mcp_permission_data_level(env):
    """data 级：全量工具（17+）。"""
    tools = list_tools(["read", "analysis", "data"])
    assert {"ingest_file", "proposal_approve", "action_create",
            "evaluation_record", "action_rollback"} <= set(tools)


def _call(server, tool: str, args: dict):
    from fastmcp import Client

    async def _run():
        async with Client(server) as client:
            result = await client.call_tool(tool, args)
            return result

    return asyncio.run(_run())


def test_mcp_read_tool_call_and_audit(env):
    """read 工具真调用 + 审计留痕（actor=mcp: 前缀）。"""
    server = build_server(["read"])
    result = _call(server, "facts_maturity", {})
    payload = json.loads(_result_text(result))
    assert "total" in payload and "immature_ratio" in payload

    with session_scope() as s:
        rows = s.scalars(
            select(AuditLog).where(AuditLog.actor.like("mcp:facts_maturity:%"))
        ).all()
        assert rows, "MCP 调用未留审计痕"


def _result_text(result) -> str:
    """fastmcp 4 CallToolResult 兼容取文本。"""
    for attr in ("data", "content"):
        v = getattr(result, attr, None)
        if v is None:
            continue
        if isinstance(v, str):
            return v
        if isinstance(v, list) and v and hasattr(v[0], "text"):
            return v[0].text
    return str(result)


def test_mcp_analysis_tool_call(env, approved_proposal):  # noqa: F811
    """analysis 级 analysis_show 冒烟（读阶段三链路产物）。"""
    from .test_stage3_decision_loop import CAMPAIGN  # noqa: F401

    server = build_server(["read", "analysis"])
    result = _call(server, "methodology_show", {"latest": True})
    payload = json.loads(_result_text(result))
    assert payload and payload[0]["version_no"] == "1.0.0"


def test_mcp_context_tool_requires_profile(env):
    """analysis_context 对无档案 SKU 返回结构化错误（不崩服务）。"""
    server = build_server(["read", "analysis"])
    result = _call(server, "analysis_context", {
        "sku": "6999", "date_from": "2026-10-01", "date_to": "2026-11-01",
    })
    payload = json.loads(_result_text(result))
    assert "error" in payload


def test_mcp_invalid_permissions_rejected(env):
    with pytest.raises(ValueError):
        build_server(["god"])
