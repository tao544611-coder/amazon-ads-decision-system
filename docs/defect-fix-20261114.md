# 缺陷修复交付报告（2026-11-14 任务单）

分支 `fix/decision-defects-20261114`（自 main @ be9c408 切出，未合并 main）。
执行原则遵守情况：先复核→再修复→最后验证；未开发新功能；未接入后台写入；未改动广告策略/成熟期规则/SKU 专项规则；方法论 v1.0.0 文件零改动。

---

## A. 修复报告（逐缺陷：复核 → 根因 → 方案 → 改动）

### A1. P0-01 观察窗口 SKU 数据隔离

**复核结果（缺陷确认）**：`create_observation()` 与 `complete_observation()` 的 MetricDaily 查询仅按 grain=date/maturity 过滤，无 SKU 过滤；任何 SKU 的观察窗都会聚合该日期范围内**全部** campaign 事实行。

**根因**：事实层早在 `facts/builder.py` 通过 CampaignMap 给 `MetricDaily.product_id` 写入了归属，但观察窗口构建时未使用该字段——归属链路存在、查询漏接。

**修改方案**：
- 新增 `_action_sku_product()`：动作 → 提案 → SKU → 产品，作为观察数据的归属锚点；提案缺失或 SKU 无产品档案时直接报错（归属不明不允许建观察）。
- 新增 `_sku_campaign_rows()`：基线与观察期共用同一过滤口径（grain=campaign + 日期窗 + mature + `product_id == 提案 SKU 产品`）；同窗内 `product_id IS NULL` 的行（活动未映射）一律**排除并计数**，写入 `baseline_json` / `post_data_json` 的 `sku_attribution`（`unattributed_campaign_rows` / `attribution_complete`）——落实「无法确认归属不得静默纳入，须标记数据不足」。
- 多 SKU 处理方式：每提案/动作唯一 SKU（Schema 单 sku 字段），多 SKU 变更须拆成多个动作分别建观察；观察窗不做跨 SKU 混合汇总（docstring 与报告均明确）。
- 保留原有日期范围、mature、grain=campaign 筛选条件不变。

**涉及位置**：`ads_core/decision/lifecycle.py`（`_action_sku_product`、`_sku_campaign_rows`、`_sku_attribution_note`、`create_observation`、`complete_observation`）。

**验收对照**：6978/6979 双 SKU 独立活动混窗 → 互不包含（合成测试）；活动缺映射 → 排除+标注；同 SKU 多活动 → 全部纳入；基线与观察期过滤口径一致；归属丢失 → 拒建观察。

### A2. P0-02 同期动作识别逻辑

**复核结果（缺陷确认）**：`(a.executed_at or "")[:10] <= end.isoformat()` 只判上界；无下界（历史动作全被误判）；无状态过滤（未执行动作 executed_at=None，空字符串恒小于窗末 → **连 pending 动作都被计入**）。

**修改方案**：
- 新增 `_concurrent_action_ids()`：`window_start <= executed_date <= window_end`（含首尾，与 complete_observation 的日期口径一致，date 对象比较杜绝字符串边界错误）。
- 状态闸门：仅 `EXECUTED_ACTION_STATUSES = ("executed", "rolled_back")` 计入——两者都真实执行过、对平台产生过影响（rolled_back 为执行后记录层回滚，其效果在窗内存在过）；pending / executing / execution_failed / cancelled 一律不计入。
- `executed_at` 日期解析失败的动作不计入（宁缺勿误报）。
- 自身动作排除（`id != action_id`）。

**涉及位置**：`ads_core/decision/lifecycle.py`（`EXECUTED_ACTION_STATUSES`、`_concurrent_action_ids`、`create_observation`）。

**验收对照**：窗前 1 天/窗首日/窗中/窗末日/窗后 1 天/40 天前六点边界 + pending/executing/failed/cancelled/rolled_back/无 executed_at/日期畸形七种状态全矩阵测试通过。

### A3. P1-01 建议校验器指标引用判断重构

**复核结果（缺陷确认）**：
1. `conversions_used` 检查的 4 个字段（current_performance/reasoning/expected_effect/problem）全是 Schema14 必填字段——Schema 关已保证非空 → 恒真 → 任何未成熟证据必触发 VR-01，与实际引用无关。
2. `_new_bid_value` 用正则从 adjustment 字符串提取数字 → `adjustment="10%"` 被解析成目标出价 £10 → VR-03 误拒。

**修改方案**：
- 建议新增可选字段 `metrics_referenced`（显式指标引用列表，白名单：impressions/clicks/spend/cpc/ctr（体量侧）+ orders/sales/conversions/units/acos/cvr/roas/cpa（转化侧）；大小写与单复数别名归一）。
- VR-01 改为按 `metrics_referenced` 与转化侧集合交集判断；**缺失/空/格式错误/未知指标名一律按安全原则视为引用了转化侧** → 未成熟证据被拒并在 detail 中说明原因（不会绕过成熟度校验）；成熟证据行不受影响（兼容旧建议读取）。
- 出价三值拆分：`current_bid` / `target_bid`（别名 `new_bid` 兼容历史建议）/ `adjustment_pct` 独立数值字段；`target_bid` 只认显式数值（bool 排除），**彻底移除从自然语言/字符串提取目标出价的路径**；`_adjustment_pct` 优先独立字段。
- 上下文构建模块 `proposal_schema` 增加 `optional_fields` 说明，AI 侧可直接得知字段语义与安全原则。

**涉及位置**：`ads_core/analysis/validator.py`（`VOLUME_METRICS`/`CONVERSION_METRICS`/`METRIC_ALIASES`、`_is_number`、`_adjustment_pct`、`_new_bid_value`、`_referenced_metrics`、`_hard_rules` VR-01/VR-02/VR-03）；`ads_core/analysis/context.py`（proposal_schema）。

**验收对照**：未成熟+仅 clicks/spend → 通过；未成熟+orders/sales → VR-01 拦截；reasoning 非空但仅体量侧 → 通过（旧缺陷回归）；`10%` 不再变 £10；0.10/1.00/3.00 边界正确；bool 不当出价；8 种格式错误全部拦截。

### A4. P1-02 YAML 方法论与程序硬规则统一（HR-08/HR-09）

**规则意图确认**：已发布 `methodology/v1.0.0.yaml` 明文——HR-08「单次建议变动相对当前 bid 约 ≤ ±15%；**更大步长须在待批里写明理由与回滚**」、HR-09「单次建议出价夹在 min≈£0.10 / max≈£2.00……**越界须在待批写明理由**」。即两规则均**允许例外、例外须说明**，属「护栏+例外声明」语义；stage2 设计文档仅写了 VR-02/03 的触发条件，未授权更严的绝对拒绝语义。意图可从已批准 YAML 直接确认，无需暂停修改。

**修改方案（程序对齐 YAML，不改 YAML）**：
- VR-02（HR-08）：幅度超 ±15% 时，若 `exception_justification` 与 `exception_rollback_plan` 同时非空 → 例外成立放行（理由与回滚方案随提案 payload 进入待批，由人工在审批门裁决）；否则拒绝，detail 写明例外条件。
- VR-03（HR-09）：出价越界时，若 `exception_justification` 非空 → 例外成立放行；否则拒绝，detail 写明例外条件。
- **方法论 v1.0.0.yaml 零改动**：YAML 语义本就允许例外，程序对齐不构成业务含义变更，按约束 7/8 不产生新版本（详见 C 部分）。
- 例外字段持久化在 `proposals_json` 的 proposal payload 中，审批时可追溯（有测试）。

**涉及位置**：`ads_core/analysis/validator.py`（`_exception_text`、VR-02/VR-03 例外分支）；`context.py` optional_fields。

**验收对照**：-20% 无例外 → 拒（detail 提示两字段）；-20% + 理由 + 回滚 → 过；-20% 仅理由 → 拒；£3.00 + 理由 → 过；£3.00 无理由 → 拒。

### A5. P1-03 MCP 审批权限与回滚语义

**复核结果（缺陷确认）**：
1. `proposal_approve` 接收调用者自填 `decided_by`；审批行无来源/验证状态字段——自报姓名无法证明身份，却以「人工审批」形态落库。
2. `rollback_action` 只改数据库状态与留痕；工具描述「回滚动作（留痕不删）」未说明**不产生平台反向操作**，存在「后台已恢复」的误读空间。

**修改方案**：
1. **审批身份**：Approval 表新增 `approval_channel`（来源）+ `identity_verified`（0/1）两列（迁移 005，幂等，历史行默认 cli/0）；`approve_proposal` 按调用面自动派生——`webgate`（本机审批门，仅监听 127.0.0.1 的人工操作页）= 已验证人工审批；`mcp` / `cli` = 未验证（decided_by 仅为自报留痕）。审计日志同步记录 channel 与 identity_verified。
2. **MCP 描述与响应**：`proposal_approve` 描述明示「本机受限操作 + decided_by 未经身份验证 + 已验证审批仅来自 webgate」，响应返回 `identity_verified=false` 与说明；模块 docstring 写明三级权限边界与「系统无身份认证能力」。
3. **权限分层**：read/analysis 级物理不注册审批/动作创建/回执/回滚/观察/验收/提案迁入/导入共 8 个写工具（新增专项测试断言）；`data` 级定位明确为「本地受限操作面，仅受信本机人工操作流程」。
4. **回滚语义**：`rollback_action` 更名 `mark_action_rolled_back`（兼容别名保留，旧调用面零改动）；docstring/工具描述/CLI help/README 统一「记录层回滚」表述；动作 `result_json` 与 MCP/CLI 响应携带 `rollback_scope="record_layer_only"`、`external_platform_restored=false`，明确不向亚马逊后台发送任何反向操作；未来真正的外部回滚列为独立需求（本次不接入）。

**涉及位置**：`ads_core/storage/models.py`（Approval 两列）、`alembic/versions/005_approval_identity.py`、`ads_core/decision/lifecycle.py`（approve_proposal/mark_action_rolled_back）、`ads_core/mcp_server.py`（docstring + 两工具）、`ads_core/cli.py`（action rollback）、`ads_core/decision/__init__.py`、`README.md`。

**验收对照**：analysis 级 8 个写工具全部不注册（测试）；MCP 审批 → identity_verified=false；webgate 审批 → identity_verified=1；回滚响应/留痕双标记；read/analysis 只读与分析工具不受影响（原 stage5 测试回归通过）。

---

## B. 测试报告（本次实际执行结果，非引用历史记录）

### B1. 总量

| 层 | 结果 |
|---|---|
| 单元测试（pytest 全量，2026-11-14 实跑） | **125 passed, 0 failed**（125.32s；原 89 全部保留并回归通过，新增 36） |
| 新增测试模块 `tests/test_defect_fixes_20261114.py` | 36/36 通过 |
| 真实历史数据验证 `scripts/validate_real_6978_6979.py` | **16/16 通过**（独立临时库实跑） |
| 未解决问题 | 无阻断性未通过项；1 项**结构性局限**（见 B4）单独记录，不在本次扩大修复 |

新增 36 项分布：P0-01 SKU 隔离 5 项、P0-02 同期动作 3 项（含 6 点边界 × 7 状态矩阵）、P1-01 指标引用 8 项 + 出价拆分 8 项、P1-02 HR 例外 6 项、P1-03 权限/身份/回滚 6 项。

### B2. 集成链路（第二层）

报表导入 → 标准化 → 事实指标 → AI 上下文 → 建议提交 → 硬规则校验 → 人工审批 → 动作记录 → 观察窗口 → 效果验收 全链由原有阶段三/四测试（`test_stage3_decision_loop.py`、`test_stage4_regression.py`、`test_stage4_support.py`）+ 新增测试共同覆盖，全绿；真实样本回归锚点（canonical 8592 / facts 8499 / immature 0.0757）未漂移。

### B3. 6978/6979 真实历史数据验证（第三层，16/16）

**数据源**：领星开放平台 `ad_sp_campaign_report`（日粒度，2026-11-02 ~ 2026-11-09 共 8 天、6978_6979 分组 7 个活动 36 行），经领星 MCP **只读**拉取（未写后台）；原始行存档 `data/raw/6978_6979_validation_20261114/`（gitignored，含 provenance：sid/profile/取数时间/字段口径）。验证脚本零真实标识（符合仓库脱敏规则）。

**关键真实发现**：6978（3 ASIN）与 6979（2 ASIN）**共用全部 7 个活动**（products.md「6978 与 6979 同类同步投放合并为一组」+ W46 补录 JSON 逐行佐证）。campaign 粒度天然无法唯一归属 → 验证脚本按修复语义**不注册 CampaignMap**，直接检验「归属不明不静默纳入」。

| # | 验证项 | 结果 |
|---|---|---|
| 1 | 两 SKU 分别导入（同批混合报表，36 行） | PASS |
| 2 | 逐 campaign×date 指标一致（引擎 vs 原始存档） | PASS（零差异） |
| 3 | 全窗合计一致：花费 £175.88 / 点击 434 / 展示 44,608 / 订单 39 / 销售 £684.00 | PASS |
| 4 | 成熟度符合 HR-01（≤11-07 成熟 28 行；11-08/09 未成熟 8 行，取数日 11-14） | PASS |
| 5 | 上下文如实标注数据缺口（无归属映射 → needs_manual_attention） | PASS |
| 6 | 观察窗 = 真实动作执行日 2026-11-09 起 14 天（[11-09, 11-23]） | PASS |
| 7 | 基线窗 28 行成熟数据全部因归属不明排除并计数标注（6978/6979 各自） | PASS |
| 8 | 观察期无成熟行（11-09 起未成熟）且不虚标归属 | PASS |
| 9 | 历史动作 SB01（2026-10-23，窗外）不干扰观察窗 | PASS |
| 10 | 同日真实动作（6979 侧 11-09）正确计入 6978 观察同期动作（反之亦然） | PASS |
| 11 | 真实业务事件「促销退出」（11-10 起，W46 补录）计入同期事件 | PASS |
| 12 | G6：同期干扰存在时强行归因被拒 | PASS |
| 13 | 记录层回滚标记 scope=record_layer_only、external_platform_restored=false | PASS |
| 14-16 | 导入批次/事实行数/规格一致性 | PASS |

**说明**：真实数据中观察窗基线/观察期行数为 0 不是缺陷——恰是修复目标行为（共用活动归属不明 → 不静默纳入 + 显式标注）；跨 SKU 数据隔离的**正向**验证（两 SKU 各有归属数据、互不包含）由合成数据测试覆盖（5 项 SKU 隔离测试）。真实数据下「分别生成建议」受限于证据锚点必须来自已归属事实行——在共用活动未映射时无可用证据，属真实数据条件而非引擎缺陷；建议校验（含未成熟拦截）由 22 项校验器测试覆盖。

### B4. 单独记录的结构性局限（不属本次修复范围）

**共用活动无法按 SKU 拆分 campaign 粒度**：6978_6979 全部 7 个活动为两 SKU 共用，CampaignMap 的 campaign→product 单归属模型在此场景下，无论映射到哪个 SKU 都会把另一 SKU 的花费/销售计入——**唯一安全行为是不映射**（本次修复后引擎已如实标注归属不明）。若需按 SKU 精确隔离，须新增「advertised_sku 粒度事实行」或「campaign 内按 ASIN 摊分」能力，属新需求，建议单独立项由负责人决策。

### B5. 权限与异常测试（第四层）

analysis 级 8 个写工具物理不注册（专项断言）；data 级 21 工具齐备；无效权限值拒绝；异常数据/缺失字段/无效状态（畸形 executed_at、bool 出价、未知指标名、格式错误引用声明、缺例外说明）均不绕过校验——见新增测试与原 T1 数据污染测试回归。

---

## C. 方法论差异报告（HR-08 / HR-09）

| 项 | HR-08（单次 bid 变动护栏） | HR-09（UK 出价上下沿） |
|---|---|---|
| YAML v1.0.0 原文语义 | ≈±15%；更大步长**须在待批写明理由与回滚**（允许例外） | £0.10–£2.00；越界**须在待批写明理由**（允许例外） |
| 修复前程序 | 直接拒绝（比已批准方法论更严） | 直接拒绝（同上） |
| 修复后程序 | 超 ±15% 时：`exception_justification` + `exception_rollback_plan` 同时非空 → 放行进待批；否则拒绝并提示例外条件 | 越界时：`exception_justification` 非空 → 放行进待批；否则拒绝并提示例外条件 |
| 与 YAML 一致性 | ✅ 一致（例外条件、审批要求、回滚要求全部落地为可校验字段） | ✅ 一致 |
| 是否产生新版本 | **否**——YAML 语义未变（本就允许例外），仅程序从「过严」对齐到「与 YAML 一致」；无业务含义变更，按版本管理约束不新增版本 | 同左 |
| 是否需要负责人确认 | 程序行为由「拒绝」变为「例外放行进待批」——放行后的裁决权仍在审批门人工；若负责人希望恢复绝对禁止语义，须**发布新版本 YAML**（明确绝对禁止），程序再随版本收紧。此为业务决策项，已留待确认 | 同左 |

结论：不存在「YAML 允许、程序无依据拒绝」（已修复），也不存在「YAML 禁止、程序却允许」（例外仅在 YAML 明文允许的「写明理由/回滚」条件下成立）。

---

## D. 权限与回滚说明

### D1. MCP 三级权限实际边界（物理不注册=最硬边界）

| 级别 | 工具 | 边界 |
|---|---|---|
| read（默认，9 个） | methodology_show / facts_maturity / batch_list / batch_show / analysis_show / proposal_list / action_list / experience_search / evaluation_stats | 纯只读 |
| analysis（+4 = 13 个） | analysis_context / analysis_validate / experience_add / experience_draft | 分析 Agent 最高权限；**物理拿不到** proposal_approve、proposal_submit、action_create、action_receipt、action_rollback、observation_create、evaluation_record、ingest_file |
| data（+8 = 21 个） | ingest_file / proposal_submit / proposal_approve / action_create / action_receipt / action_rollback / observation_create / evaluation_record | **本地受限操作面**：仅受信本机人工操作流程；系统当前无身份认证能力，参数中的姓名均为自报 |

### D2. 审批身份验证能力（如实陈述）

- 系统当前**没有**可信身份认证能力（无登录/签名/_challenge）。
- `decided_by` 是调用者自报姓名，**不视为已验证身份**。
- 审批行如实记录：`approval_channel`（webgate/cli/mcp）+ `identity_verified`（0/1）+ `decided_at` + 审计日志（actor 含调用面与权限级）。
- **identity_verified=1 仅出现在 webgate 通道**：本机审批门仅监听 127.0.0.1:8765、无外部暴露，操作者为在本机打开页面的运营本人——本系统当前唯一的「已验证人工审批」通道，其验证边界=本机物理访问。
- MCP 工具描述、README、审批响应三处均明示上述限制，不宣称已实现身份认证。

### D3. 回滚实际作用范围（如实陈述）

- `mark_action_rolled_back`（原 rollback_action 兼容别名保留）只做三件事：本地动作状态→rolled_back、写入回滚原因/操作人/时间/scope 标记、审计留痕；历史不删不覆盖。
- **不向亚马逊后台发送任何反向操作**；`external_platform_restored` 恒为 false；不代表广告原始状态已恢复。
- 外部实际恢复须另行人工在后台操作并经 `action_receipt` 核验；真正的外部回滚列为独立需求，本次未接入（遵守「不接入后台写入接口」约束）。

---

## E. Git 交付

- **分支**：`fix/decision-defects-20261114`（基于 main @ be9c408）
- **变更文件**：
  - 修复：`ads_core/decision/lifecycle.py`、`ads_core/analysis/validator.py`、`ads_core/analysis/context.py`、`ads_core/mcp_server.py`、`ads_core/cli.py`、`ads_core/storage/models.py`、`ads_core/decision/__init__.py`
  - 迁移：`ads_core/alembic/versions/005_approval_identity.py`（新增，幂等）
  - 测试：`tests/test_defect_fixes_20261114.py`（新增 36 项）
  - 脚本：`scripts/validate_real_6978_6979.py`（真实数据验证，零真实标识）
  - 文档：`README.md`、`docs/defect-fix-20261114.md`（本文件）
  - 不入库：`data/raw/6978_6979_validation_20261114/`（原始存档，gitignored）
- **测试结果**：125/125 全绿（2026-11-14 实跑）+ 真实数据 16/16
- **合并建议**：建议合并（全部 P0/P1 验收条件满足，见任务单第七节逐项核对）；合并前请负责人复核 C 部分「是否需要绝对禁止语义」的业务决策项。**不自行合并 main，不自行开展真实广告执行。**

## 最终验收条件核对

- [x] 6978 和 6979 的观察数据完全隔离（合成 5 项 + 真实共用活动归属不明排除）
- [x] 同期动作只识别观察窗口内真实执行的动作（13 组边界/状态矩阵 + 真实时间线）
- [x] 建议校验能够准确识别实际引用的指标（metrics_referenced 显式声明 + 安全原则）
- [x] 出价与调价比例不再混淆（三值拆分，10%≠£10 有回归测试）
- [x] YAML 与程序硬规则保持一致（HR-08/09 例外语义对齐，YAML 零改动）
- [x] 普通分析 Agent 无法调用审批和执行权限（物理不注册 + 专项断言）
- [x] 回滚功能没有夸大实际能力（记录层回滚三处文档 + 响应双标记）
- [x] 新增测试全部通过（36/36 + 16/16）
- [x] 原有回归测试全部通过（89/89，锚点无漂移）
- [x] 6978 和 6979 的真实历史数据验证通过（16/16，指标与原始报表零差异）
- [x] 没有引入未经批准的广告策略变更（零后台写入；方法论 v1.0.0 零改动）
