# 阶段三详细设计 · 审批与观察闭环

> - **版本**：v1.0
> - **日期**：2026-11-14
> - **上游依据**：PRD 4.8–4.11、5.1 步 12–17、第十章阶段三、T6/T7/T8；架构评审 D6（动作幂等键）、D7（FTS5 二元切分）、D8（审计）
> - **阶段三目标（PRD 原文）**：一条广告建议能够从提出、审批、观察一直追踪到最终验收

---

## 0. 边界重申

- 本阶段无后台写入：动作只有 `simulated`（模拟执行）与 `manual`（人工执行记录）两种模式（PRD 4.9 第一阶段目标）；后台接口接入后的独立授权机制是后续阶段
- 审批与执行分离（T6）：approval 只产生授权记录；执行须另建 action 并引用 approval
- 证据不足不得强行判定（T7）：verdict 四值中 `insufficient_evidence` 是合法终态

## 1. 数据模型（迁移 `003_stage3_decision_loop`，7 张表）

```sql
proposal(          -- 提案（来源：analysis_run.proposals_json 通过项）
  id PK, analysis_run_id FK NOT NULL, proposal_index INTEGER NOT NULL,
  sku TEXT NOT NULL, target TEXT NOT NULL, action_type TEXT NOT NULL,
  current_value_json TEXT, proposed_value_json TEXT, adjustment TEXT,
  payload_json TEXT NOT NULL,               -- 完整建议（14 字段）
  methodology_version_id FK NOT NULL, profile_version_id FK NOT NULL,  -- 自 run 继承
  status TEXT NOT NULL DEFAULT 'pending_approval',
    -- pending_approval → approved | approved_modified | rejected | deferred
    -- deferred → pending_approval（可重新提请）
  created_by TEXT NOT NULL, created_at,
  UNIQUE(analysis_run_id, proposal_index) )

approval(          -- 审批记录（人工；决策与终稿分离保存，PRD 4.8）
  id PK, proposal_id FK NOT NULL,
  decision TEXT NOT NULL,                    -- approve|modify_approve|reject|defer
  decided_by TEXT NOT NULL, decided_at TEXT NOT NULL,
  final_payload_json TEXT NOT NULL,          -- 运营修改后终稿（必须保存，不能只留 AI 初稿）
  comment TEXT, created_at )

action(           -- 广告动作（D6：idempotency_key 唯一，不重复执行 T8）
  id PK, proposal_id FK NOT NULL, approval_id FK NOT NULL,
  action_type TEXT NOT NULL, target_json TEXT, original_value_json TEXT, final_value_json TEXT,
  execute_mode TEXT NOT NULL,                -- simulated|manual
  status TEXT NOT NULL DEFAULT 'pending',
    -- pending → executing → executed | execution_failed
    -- executed → rolled_back；pending → cancelled
  approved_by TEXT NOT NULL, executor TEXT NOT NULL, executed_at TEXT,
  result_json TEXT, verify_status TEXT,      -- unverified|verified|state_unknown
  rollback_policy_json TEXT,                 -- {risk_trigger, rollback_condition, rollback_target, needs_manual_confirm}
  idempotency_key TEXT NOT NULL UNIQUE,      -- proposal_id+approval_id 派生
  created_at )

execution_receipt( -- 执行回执（可多条=重试历史；无回执时须先查状态，T8）
  id PK, action_id FK NOT NULL,
  receipt_type TEXT NOT NULL,                -- simulated|manual|agent
  operator TEXT NOT NULL, operated_at TEXT NOT NULL,
  result TEXT NOT NULL,                      -- success|failed|unknown
  before_state_json TEXT, after_state_json TEXT, raw_json TEXT,
  received_at TEXT NOT NULL, created_at )

observation(      -- 观察窗口（一动作一观察）
  id PK, action_id FK NOT NULL UNIQUE,
  window_start TEXT NOT NULL, window_end TEXT NOT NULL,   -- F5: max(验证窗, 归因满窗)
  baseline_json TEXT NOT NULL,               -- 动作前基准事实快照
  post_data_json TEXT,                       -- 动作后数据快照
  concurrent_events_json TEXT,               -- 同期业务事件（自动拉取，T7）
  concurrent_actions_json TEXT,              -- 同期其他广告动作（T7）
  status TEXT NOT NULL DEFAULT 'open',       -- open|window_complete|evaluated
  created_at, updated_at )

evaluation(       -- 效果验收（四裁决，T7 归因分层）
  id PK, action_id FK NOT NULL UNIQUE, observation_id FK NOT NULL,
  verdict TEXT NOT NULL,                     -- valid|invalid|negative|insufficient_evidence
  attribution_json TEXT NOT NULL,            -- {what_changed[], possibly_related[], strongly_supported[], other_factors[]}
  evidence_ids_json TEXT, maturity_note TEXT,
  decided_by TEXT NOT NULL, decided_at TEXT NOT NULL, created_at )

experience(       -- 经验库（失败经验必须保留，PRD 4.11）
  id PK, exp_code TEXT NOT NULL UNIQUE,      -- 如 EXP-2026-11-14-001
  product_id FK, scenario TEXT NOT NULL, original_problem TEXT NOT NULL,
  ai_judgment TEXT, operator_correction TEXT, actual_action TEXT, observation_result TEXT,
  evidence_strength TEXT NOT NULL,           -- strong|medium|weak
  applicable_when_json TEXT, not_applicable_when_json TEXT,
  status TEXT NOT NULL DEFAULT 'pending_verification',   -- verified|pending_verification|failed
  related_action_id TEXT, created_by TEXT NOT NULL, created_at, updated_at )
-- + FTS5 虚表 experience_fts(exp_id UNINDEXED, text)，代码双写，中文按字符二元切分（D7）
```

## 2. 决策链状态机与守卫

```
analysis_run(passed) ──submit──► proposal(pending_approval)
                                     │ 人工审批（approve / modify_approve / reject / defer）
        ┌────────────────────────────┼──────────────────────────┐
     rejected/deferred          approved / approved_modified
     （终态/可重提）                   │ create_action（须有 approval，T6）
                                       ▼
                                action(pending → executing → executed/failed)
                                       │ receipt（回执核验；无回执→verify_status=state_unknown，禁止重执行 T8）
                                       │ executed + rollback 条件 → rolled_back（历史保留）
                                       │ create_observation（F5 窗口 + 基准 + 同期事件/动作）
                                       ▼
                                observation(open → window_complete → evaluated)
                                       │ record_evaluation（四裁决 + 归因分层，T7）
                                       ▼
                                evaluation → experience（可选沉淀）
```

**守卫（程序强制）**：
- G1（T6）：`create_action` 要求 proposal.status ∈ {approved, approved_modified} 且引用对应 approval.id；AI 不能自批（approver 与提案创建者分离校验：`decided_by != created_by` 时警告不改行为——单人运营场景 Gordon 可自批，记录留痕）
- G2（T6）：无 approval 的 proposal 不能进 action；rejected/deferred 永不进
- G3（T8）：`idempotency_key = proposal_id + ":" + approval_id` 唯一——同提案同审批重复建动作直接返回既有 action（幂等，Agent 中断重跑安全）
- G4（T8）：action 无成功回执时不可进入 observation/rollback 以外状态转换；`receipt(result=unknown)` → `verify_status=state_unknown`，提示「先查后台实际状态，不盲目重执行」
- G5（F5）：观察窗 `window_end = max(executed_at + 验证窗(7 天), executed_at + 归因满窗(SP7/SB·14))`，参数取方法论 `acceptance.window_default`/`attribution_maturity`
- G6（T7）：evaluation 窗口内存在 concurrent_events/actions 时，attribution_json 必须填写 other_factors 或将 verdict 置 insufficient_evidence（程序只校验结构非内容）

## 3. 上下文包增强（阶段二接口回填，D3 原设计）

`assemble_analysis_context` 增补 `related_experience`：FTS5 检索（sku + 场景词）取 top 5 条已验证/待验证经验入包，标注「辅助依据，不覆盖正式方法论」（PRD 4.11）。

## 4. CLI（全部 --json）

```
ads proposal submit <run_id> [--only passed] [--by]        # 通过项迁入提案
ads proposal list [--status] / show <id>
ads proposal approve <id> --decision approve|modify|reject|defer [--file 修改稿] [--comment] --by
ads action create <proposal_id> --mode simulated|manual [--rollback-file] --by
ads action receipt <action_id> --result success|failed|unknown [--before --after --raw] --by
ads action rollback <action_id> --reason --by
ads observation create <action_id> --by / complete <observation_id|action_id> --by
ads evaluation record <action_id> --verdict ... --attribution-file --by
ads experience add --file <yaml> --by / search <query> / list [--status]
```

## 5. 测试设计（T6/T7/T8 + 经验）

| 组 | 断言 |
|---|---|
| test_t6_permission | 未审批建动作被拒；rejected 永不进；审批与执行记录分离（action.approval_id 必填且指向真实 approval）；修改后终稿保存 |
| test_t7_attribution | observation 自动拉取同期事件+动作；有干扰因素时 evaluation 缺 other_factors 被拒；insufficient_evidence 合法落库 |
| test_t8_rollback | 幂等键：同提案同审批重复 create 返回同一 action；回执 unknown → state_unknown 且不可重执行；rollback 后状态与历史完整保留 |
| test_observation_window | F5 窗口=max(7 天验证窗, 14 天归因满窗)边界；baseline/post 快照内容 |
| test_experience | 添加/中文检索命中/失败经验不删/上下文包含 related_experience |

## 6. 开放问题

| # | 问题 | 默认方案 |
|---|---|---|
| O3-1 | 自批场景（Gordon 单人运营）approver=creator | 允许但审计留痕 + 守卫记录 warned_self_approval=true |
| O3-2 | 观察窗自动完结（窗口到期拉数） | 阶段三手动 `complete`；自动调度属阶段五 |
| O3-3 | 回滚自动触发 | 永不自动执行（PRD 4.10 模拟回滚+人工处理）；evaluation 触发条件时输出回滚建议 |
