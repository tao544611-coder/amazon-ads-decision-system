# 阶段五详细设计 · MCP 开放与能力扩展

> - **版本**：v1.0 · 2026-11-14
> - **上游**：PRD 第十章阶段五（本地 MCP Server / 标准化工具 / 读取-分析-提案权限 /
>   调用日志 / 多 Agent 接入 / 核心引擎独立）；架构评审 D1（MCP=service 层薄适配）
> - **核心约束**：MCP 层零业务逻辑——只做参数适配 + 权限过滤 + 调用留痕，
>   全部业务在 service 层（保持引擎可独立测试，D3 精神）

## 1. 工具与权限分级（物理不注册 = 最硬边界）

启动：`ads mcp serve --permissions read,analysis[,data]`（默认 read）

| 级别 | 工具（对应 CLI/service） | 说明 |
|---|---|---|
| **read**（默认） | `methodology_show` / `facts_maturity` / `batch_list` / `batch_show` / `proposal_list` / `action_list` / `experience_search` / `analysis_show` / `evaluation_stats` | 只读，无副作用 |
| **analysis** | `analysis_context`（组装上下文包）/ `analysis_validate`（校验建议）/ `experience_add` / `experience_draft` | 产生分析运行/经验（写分析域表，不碰决策链） |
| **data** | `ingest_file` / `product_add` / `profile_update` / `map_campaign` / `event_add` / `rank_add` / `proposal_submit` / `proposal_approve` / `action_create` / `action_receipt` / `action_rollback` / `observation_create` / `observation_complete` / `evaluation_record` | 高危：数据导入与决策链写操作 |

未列级别的工具不注册（客户端不可见）。审批类（proposal_approve / action_*）
建议只在受信本机以 data 级启动。

## 2. 调用留痕

统一包装：每次 MCP 工具调用写 `audit_log`（actor=`mcp:<tool>:<permissions>`，
details 含参数摘要——**密钥/文件二进制内容不入日志**）。MCP 调用与 CLI 调用在
审计流同源可区分。

## 3. 传输与生命周期

- stdio 传输（本地 Harness 拉起子进程）；`ads mcp serve` 不退出（服务进程）
- `ads mcp list-tools --permissions ...`：本地自检列出将注册的工具（不启动服务）

## 4. 测试

`tests/test_stage5_mcp.py`：in-process 取 FastMCP 实例——
- 权限过滤：read 级不含任何写工具；analysis 级含 context/validate 不含 ingest
- 工具调用冒烟：read 级 `facts_maturity` / analysis 级 `analysis_context` 真调用返回
- 审计留痕：调用后 audit_log 出现 `mcp:` 前缀记录
