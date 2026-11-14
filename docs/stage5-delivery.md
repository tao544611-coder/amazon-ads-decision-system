# 阶段五交付文档（MCP 开放）

> 交付日期：2026-11-14 · 上游依据：`stage5-detailed-design-v1.0.md` · fastmcp 4.2.0

## 已完成

| 项 | 内容 |
|---|---|
| MCP Server | `ads mcp serve`（stdio，本地 Harness 拉起）；零业务逻辑——service 层薄适配（D1/D3 精神） |
| 权限分级 | `--permissions read,analysis[,data]`：**物理不注册**（客户端不可见）。read 9 工具（只读）→ +analysis 13（上下文/校验/经验）→ +data 21（导入+决策链写） |
| 调用留痕 | 每次调用写 `audit_log`（actor=`mcp:<tool>:<perms>`，参数摘要截断 80 字符，密钥/二进制不入日志）；与 CLI 调用同源可区分 |
| 本地自检 | `ads mcp list-tools --permissions ...`（不启动服务列工具） |
| 错误隔离 | 工具内异常结构化返回（如 analysis_context 对无档案 SKU 返回 error JSON，服务不崩） |

## 三级工具表（实测）

- **read（默认 9）**：methodology_show / facts_maturity / batch_list / batch_show / analysis_show / proposal_list / action_list / experience_search / evaluation_stats
- **+analysis（13）**：analysis_context / analysis_validate / experience_add / experience_draft
- **+data（21）**：ingest_file / proposal_submit / proposal_approve / action_create / action_receipt / action_rollback / observation_create / evaluation_record

## 测试

`test_stage5_mcp.py` 7 用例：三级权限过滤（read 无写工具 / analysis 无数据写 / data 全量）、in-memory Client 真调用、审计留痕（mcp: 前缀）、错误结构化返回、非法权限拒绝。

## 已知限制

- 审批/执行类工具（proposal_approve 等）建议仅在受信本机以 data 级启动——MCP 层无操作者身份认证（本地单用户场景；联网暴露需另行加鉴权，当前不属范围）
- stdio 传输为唯一通道（SSE/HTTP 未开，本地场景够用）

## Harness 接入示例

```bash
ads mcp serve --permissions read,analysis        # 分析型 Agent
ads mcp serve --permissions read                 # 只读监控型 Agent
```
