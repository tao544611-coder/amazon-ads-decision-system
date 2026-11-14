# 阶段二详细设计 · 广告分析与方法论接入

> - **版本**：v1.0
> - **日期**：2026-11-14
> - **上游依据**：[requirements-baseline.md](./requirements-baseline.md)（PRD 4.7 分析七步 / 5.1 流程 6-11 步 / 第十章阶段二 / T4+T5 验收）、[architecture-review-v0.2.md](./architecture-review-v0.2.md)（D3 AI 在引擎外、D4 建议校验器、D5 版本快照引用）
> - **前置**：阶段一已交付（数据管道 + 事实指标 + 档案 + 方法论入库，38 测试全绿）
> - **阶段二目标（PRD 原文）**：AI 能够基于产品背景和历史数据形成**可追溯**的广告优化建议

---

## 0. 边界重申（本文不再论证）

- AI 在引擎外（D3）：引擎只做「上下文组装」与「建议校验」两件事，零 LLM 依赖；AI（Harness 侧）读上下文包 → 产建议 JSON → 交回校验
- 校验三关（D4）：JSON Schema → 硬规则 → 证据存在性；任一关失败 → `rejected_by_validator`，不进审批（审批本身是阶段三）
- 本阶段不建：proposal/approval/action 等决策链表（阶段三）；校验通过的建议落 `analysis_run.proposals_json`（暂存，阶段三迁入正式提案表）

## 1. 数据模型（迁移 `002_stage2_analysis`）

```sql
-- analysis_run：一次分析运行（D5：快照引用，历史分析可还原当时背景）
analysis_run(
  id PK, sku TEXT NOT NULL,
  date_from TEXT NOT NULL, date_to TEXT NOT NULL,
  marketplace TEXT NOT NULL DEFAULT 'UK',
  status TEXT NOT NULL DEFAULT 'context_ready',
    -- context_ready（上下文已组装）→ validating →
    --   proposals_validated（有建议过校验）| validated_none（建议全拒/未提交）| failed
  methodology_version_id FK→methodology_version NOT NULL,
  profile_version_id FK→product_profile_version NOT NULL,   -- 分析时引用的档案版本（T5 可追溯）
  batch_ids_json TEXT NOT NULL,        -- 参与事实计算的批次集合
  context_json TEXT NOT NULL,          -- 完整上下文包（冻结快照）
  context_flags_json TEXT,             -- {immature_ratio, confounding_events:[...], data_gaps:[...]}
  proposals_json TEXT,                 -- 校验后的建议列表（含每条 verdict+拒绝原因）
  created_by TEXT NOT NULL, created_at, completed_at )

-- evidence：证据登记（建议引用 → 库内实体，T5 可追溯锚点）
evidence(
  id PK, analysis_run_id FK→analysis_run NOT NULL,
  evidence_type TEXT NOT NULL,   -- metric_daily | business_event | keyword_rank | methodology_rule | product_profile
  ref_id TEXT NOT NULL,          -- 对应表行 id
  snapshot_json TEXT,            -- 引用时的关键内容快照（防后续数据变化失据）
  created_at,
  UNIQUE(analysis_run_id, evidence_type, ref_id) )
```

## 2. 上下文组装器 `assemble_analysis_context()`（PRD 4.7 七步的引擎侧落地）

```
ads analysis context --sku 6978 --from D --to D [--grains ...] [--top-terms N]
```

| 步 | PRD 4.7 | 引擎实现（确定性） |
|---|---|---|
| 1 数据条件 | 完整性/窗口/口径/样本量/成熟度 | 汇总 metric_daily（mature/immature 分组统计）+ `context_flags.data_condition`；**转化侧未成熟行强制隔离**（HR-01/02：orders/sales/acos/cvr 数据只出现在 `facts_immature` 观察区，并带 `maturity=immature_observation` 标注） |
| 2 产品档案 | 定位/毛利/目标/竞品/词/特殊规则 | 当前 profile_version payload 全量（A–F 组） |
| 3 历史变化 | 指标环比/自然位/竞位/活动变化 | 窗口二等分前后段对比（spend/clicks/orders/acos/ctr/cvr，仅成熟行）；keyword_rank_snapshot 按关键词取最近两次对比 |
| 4 业务事件 | 秒杀/调价/断货等干扰 | `events_in_window` 全量 + `context_flags.confounding_events`（活跃事件列表） |
| 5 原因判断框架 | 区分事实/可能/假设/无法判断 | 上下文输出 `judgment_requirements`：要求 AI 分层结论 + 禁止单指标归因（结构化提示，见 §4 T4-4） |
| 6 方法论 | 按情况调用规则 | hard_rules（ingest_gate+validator）全文 + 参数组 5 个（target_acos/zero_conversion/bid_adjustment/cvr_estimation/acceptance）+ knowledge 全文 |
| 7 建议生成 | —— | 输出 `proposal_schema`（14 字段）+ `evidence_ids` 用法说明 |

**上下文包结构**（`context_json`，密钥永不出现在包内，D9 白名单组装）：

```json
{
  "meta": {sku, marketplace, date_range, generated_at, methodology_version_no, profile_version_no},
  "data_condition": {成熟度统计 by type, 口径, 批次引用, data_gaps},
  "product_profile": {...payload v(n)},
  "facts": {
    "campaign": [ {fact_id, date, campaign_id/name, ad_product_type, 度量, 派生指标, maturity} ],
    "searchterm_top": [ 按 spend 降序前 N（默认 50），含 computable 标记 ],
    "comparison": {前段 vs 后段（仅成熟行）}
  },
  "keyword_ranks": [ {keyword, current, previous, delta} ],
  "business_events": [ {event_id, type, starts_at, ends_at, description} ],
  "methodology": {hard_rules, parameters, knowledge, one_line_conclusion},
  "context_flags": {immature_ratio, confounding_events, needs_manual_attention},
  "judgment_requirements": {分层结论要求, 单指标禁令, 证据引用要求},
  "proposal_schema": {...14 字段定义与示例}
}
```

## 3. 建议校验器 `validate_proposal()`（D4 三关）

```
ads analysis validate <run_id> --file proposal.json   # 单条或多条
```

**第一关 Schema（14 必填字段，PRD 4.7 十三项 + evidence_ids）**：
`sku`、`target`（活动/关键词）、`current_performance`、`problem`、`fact_evidence_ids[]`、`event_evidence_ids[]`、`reasoning`（原因分析）、`action`、`adjustment`（幅度）、`expected_effect`、`risks`、`observation_window`、`acceptance_criteria`、`needs_approval`（恒 true，阶段三起效）。缺任一 → `rejected_schema`。

**第二关 硬规则**（enforcement=validator / ingest_gate，参数全部取自方法论参数组，D10）：

| 码 | 规则 | 依据 |
|---|---|---|
| VR-01 | 引用了未成熟行的**转化侧指标**作为结论依据（fact_evidence 中 maturity=immature 的行出现在 reasoning/expected_effect 的证据位） | HR-01/02 |
| VR-02 | bid 类建议幅度 \|adjustment\| > max_single_change_pct(15%) | bid_adjustment |
| VR-03 | bid 类建议目标出价超出 [uk_floor(0.10), uk_ceiling(2.00)] | bid_adjustment |
| VR-04 | bid 类建议且样本 clicks < min_clicks_for_bid_action(20)，且 action ≠ 观察/维持类 | bid_adjustment（2026-10-30 改 20） |
| VR-05 | 存在活跃干扰事件（confounding_events 非空）但 event_evidence_ids 为空（未考虑业务事件影响） | T4 验收「能说明业务事件对判断的影响」 |
| VR-06 | sku ≠ run.sku 或 target 不在事实数据中 | 一致性 |

任一命中 → `rejected_hard_rule` + 违规码。VR-05 命中时若 risks/reasoning 明确引用了事件 id 则放行（引用即可，不判内容——判断质量归 AI，阶段四人工核对兜底）。

**第三关 证据存在性**：`fact_evidence_ids`/`event_evidence_ids` 每个 id 必须存在于 `evidence` 表且属于本 run；`rule` 引用（reasoning 中的 rule_code）必须在方法论库中存在。失败 → `rejected_missing_evidence`。

**通过** → verdict=`passed`，写入 `analysis_run.proposals_json`；全部结果（含拒绝原因）随 `--json` 返回并落审计。

## 4. 测试设计（T4 + T5，pytest 合成数据）

| 用例组 | 场景 | 断言 |
|---|---|---|
| test_t4_product_context | ①录入秒杀事件+断货事件 → 组装上下文 ②关键词排名两次快照 | ①events/confounding_events 在包内且 flags 置位 ②ranks 含 current/previous/delta |
| test_t4_single_metric_guard | 活跃事件窗口内的建议未引用事件 | VR-05 拒绝（不因单一指标直接形成结论的引擎侧闸门） |
| test_t5_evidence_trace | 建议引用 fact_id/event_id/rule_code | ①存在的全通过 ②伪造 id → rejected_missing_evidence ③建议追溯 profile_version_id/methodology_version_id（run 表断言） |
| test_validator_hard_rules | 构造 6 类违规建议（未成熟转化证据/幅度 20%/bid 3£/clicks<20/未知 target/sku 错） | 逐条对应 VR-01~06 拒绝 |
| test_context_maturity_isolation | 同窗含未成熟行 | 转化侧未成熟行只出现在隔离区；comparison 只用成熟行 |

## 5. CLI 增量（全部 --json）

```
ads analysis context  --sku --from --to [--grains] [--top-terms N] [--by]
ads analysis show <run_id>
ads analysis validate <run_id> --file proposal.json [--by]
```

## 6. 开放问题

| # | 问题 | 默认方案 |
|---|---|---|
| O2-1 | searchterm 上下文深度（全量 vs topN） | 默认 spend 降序 top 50（参数 --top-terms） |
| O2-2 | 冷却期硬规则（D4 提及）无方法论参数 | 阶段三动作侧实现，校验器预留 VR 码段 |
| O2-3 | advice 通过后的暂存位置 | proposals_json；阶段三迁入 proposal 表（不返工：字段对齐） |
