# 阶段二交付文档（按 PRD 十一章模板）

> 交付日期：2026-11-14 · 上游依据：`stage2-detailed-design-v1.0.md` · 前置：阶段一（38 用例全绿）

## 一、已完成功能

| 模块 | 内容 | PRD 对应 |
|---|---|---|
| 数据模型 | 迁移 `002_stage2_analysis`：`analysis_run`（快照引用：方法论版本+档案版本+批次集合，D5）+ `evidence`（证据登记，T5 锚点） | 4.7 / D4/D5 |
| 上下文组装器 | `assemble_analysis_context()`：PRD 4.7 七步确定性落地——数据条件（成熟度统计+口径）→ 档案 → 窗口二等分历史对比（仅成熟行）→ 自然位两次快照对比（up/stable/down）→ 业务事件 → 方法论三层（19 硬规则+7 参数组+14 知识）→ 建议模板（14 字段 Schema）；密钥白名单组装（D9） | 4.7 七步 / 5.1 步 6-11 |
| 成熟度隔离 | HR-01/02 强制：未成熟行全部带 `maturity` 标注入包；comparison 只用成熟行；`immature_policy` 写入包内说明 | T3/T4 |
| 建议校验器 | `validate_proposal()` 三关：①Schema 14 必填字段 ②硬规则 VR-01~06（未成熟转化证据/幅度>15%/出价越界[0.10,2.00]/点击<20 非观察类/活跃事件未引用/sku 不符，参数全部取自方法论）③证据存在性（fact/event id 须在本 run evidence 中、reasoning 引用规则码须在库） | D4 / T5 |
| CLI | `ads analysis context / show / validate`（全 `--json`，`--dump` 可输出完整上下文包） | Q1 |
| 审计 | 组装/校验全落 `audit_log` | 7.3 |

## 二、代码与目录

```
ads_core/ads_core/analysis/{context.py, validator.py, __init__.py}
ads_core/alembic/versions/002_stage2_analysis.py
tests/test_stage2_analysis.py（14 用例）
```

## 三、测试用例与运行结果

**命令**：`.venv/Scripts/python.exe -m pytest tests/ -q`
**结果：52 passed, 0 failed**（阶段一 38 + 阶段二 14，2026-11-14 本机）

| 组 | 数 | 覆盖 |
|---|---|---|
| T4 产品背景 | 3 | 事件+排名快照入包且 flags 置位 / VR-05 单指标闸门（活跃事件未引用即拒）/ 未成熟行隔离与 comparison 过滤 |
| 校验器 | 7 | 合法建议通过 / VR-01/02/03/04（含观察类放行分支）/VR-06 / Schema 缺字段 |
| T5 决策依据 | 3 | 伪造证据 EV-01 拒 / 未知规则码 EV-02 拒 / run 可追溯（方法论+档案版本+批次集合+evidence 计数） |
| 边界 | 1 | 无档案 SKU 拒绝组装（关键数据缺失暂停，PRD 5.1） |

## 四、真实库端到端冒烟（2026-11-14）

1. `ads analysis context --sku 6978 --from 2025-12-01 --to 2026-01-05` → `context_ready`，evidence 20 条（19 硬规则+档案 v2）；`data_gaps` 如实标注「映射活动无报表数据」（样本实为约德账号，已证）
2. 提交含伪造证据的建议 → `rejected_missing_evidence` + `EV-01: 证据 id 不存在或不属于本次分析`（T5「无法确认时明确标记证据不足」引擎侧行为）

## 五、已知问题

1. VR-01 对「未成熟证据」的判定以行级 maturity 为准；若 AI 把未成熟数字写进 reasoning 文本而证据行本身成熟，引擎无法程序化识别（判断质量归 AI + 阶段四人工核对，架构评审 D4 自洽说明）
2. 冷却期硬规则（D4 提及）无方法论参数，阶段三动作侧实现（O2-2）
3. 建议通过后暂存 `analysis_run.proposals_json`；阶段三迁入正式 proposal 表（字段已对齐，O2-3）
4. 真实库当前无 6978/6979 报表数据，上下文包 facts 区为空 + data_gaps 标注（真实结果，待补昕信报表）

## 六、未实现（阶段二范围外）

- proposal/approval/action/execution_receipt/observation/evaluation/experience 表与闭环（阶段三，T6/T7/T8）
- MCP Server 封装（阶段五）；FTS5 经验检索（阶段三经验库）
- CVR 加权估算（`cvr_estimation` 参数已入上下文包供 AI 参考；加权计算属分析判断，不在引擎内实现）

## 七、下阶段入口

阶段三（审批与观察闭环）：`proposal`+`approval`+`action`+`execution_receipt`+`observation`+`evaluation`+`experience` 七表迁移 → 提案状态机（含 `rejected_by_validator`→`draft` 回流）→ 观察窗口状态机（F5：观察窗=max(验证窗, 归因满窗)）→ 回滚流程 → 经验库 FTS5。
