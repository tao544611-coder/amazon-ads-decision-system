# 阶段一详细设计 · 核心数据与基础架构

> - **版本**：v1.0
> - **日期**：2026-11-14
> - **上游依据**：[requirements-baseline.md](./requirements-baseline.md)（PRD V1.0 第十章·阶段一 / 第八章 MVP / 第九章 T1–T3）、[architecture-review-v0.2.md](./architecture-review-v0.2.md)（D1–D11 设计决策）、[../methodology/v1.0.0.yaml](../methodology/v1.0.0.yaml)（已发布规则源 v1.0.0）
> - **性质**：详细设计文档，**本阶段不含业务代码**；按 PRD 十一章，开发按本设计实施并随附测试证据
> - **阶段一目标（PRD 原文）**：建立独立的本地业务系统——数据库结构、原始文件存储、数据导入、标准化与质量检查、指标计算、产品档案与历史记录；交付「可以独立完成广告数据导入、检查和事实指标生成」

---

## 0. 已定决策基线（本文不再重复论证）

| 决策 | 内容 | 出处 |
|---|---|---|
| D1 | CLI（typer）为第一入口，全部命令支持 `--json`；Harness 插件调 CLI | 评审 v0.2 · Q1 |
| D2 | SQLAlchemy 2.0 + Alembic；SQLite WAL；`foreign_keys=ON` | 评审 v0.2 |
| D6 | 导入幂等：`file_hash` 唯一；不静默覆盖 | 评审 v0.2 |
| D10 | 时间=报表原生日期 + marketplace 标注；金额带 currency；T-2/成熟度参数取自方法论 | 评审 v0.2 |
| D11 | 管道三段：源适配器 → canonical 统一层 → 事实层；事实层禁读源报表 | 评审 v0.2 · Q3 |
| F1–F5 | 方法论权威链=Notion 页；UK/GBP；SKU 例外归产品档案；外部资产新环境重建；观察窗=max(验证窗, 归因满窗) | 方法论 v1.0.0 |
| HR-01/02/04 | 成熟度 SP≥7 天 / SB·SD≥14 天 / 混算从严 / T-2 只挡报表延迟 | 方法论 v1.0.0 |

---

## 1. 数据模型设计

### 1.1 表清单与分期

| 分期 | 表 | PRD 对象归属 |
|---|---|---|
| **阶段一必建（21 张）** | `product`、`product_profile_version`、`campaign_map`、`business_event`、`import_batch`、`raw_dataset`、`report_identification`、`quality_finding`、`can_campaign_daily`、`can_adgroup_daily`、`can_target_daily`、`can_searchterm_daily`、`can_placement_daily`、`can_advertised_product_daily`、`can_purchased_product_daily`、`metric_daily`、`keyword_rank_snapshot`、`methodology_version`、`methodology_rule`、`audit_log`、`alembic_version`（迁移自带） | Product / ProductProfile / BusinessEvent / RawDataset / NormalizedDataset / MetricSnapshot / KeywordSnapshot / Methodology / RuleConfig /（补充：ImportBatch、AuditLog、MethodologyRule） |
| 阶段二建 | `analysis_run`、`evidence` | AnalysisRun |
| 阶段三建 | `proposal`、`approval`、`action`、`execution_receipt`、`observation`、`evaluation`、`experience` | Proposal…Experience |
| 阶段四建 | `competitor_rank_snapshot`（表结构随阶段三一并设计，避免返工） | CompetitorProfile 相关 |

> 说明：PRD 6 章的 18 对象中，`NormalizedDataset` 落地为 7 张 `can_*_daily` 表（按报表粒度分表，杜绝「口径混加」——HR-13 在 schema 层的体现）；`RuleConfig` 落地为 `methodology_version` + `methodology_rule`（YAML 导入）；`CompetitorProfile` 首期作为产品档案 D 组字段承载，独立表推迟（PRD 8.3 暂不做竞品自动监控）。

### 1.2 字段设计（DDL 级）

约定：PK=`id TEXT`(uuid4)；时间戳 `TEXT ISO8601 UTC`；金额/数值 `REAL`；枚举 `TEXT + CHECK`；所有表带 `created_at`。

**产品域**

```sql
-- product：产品基础信息（PRD 4.2.A）
product(
  id PK, sku TEXT UNIQUE NOT NULL,          -- '6978' / '6979'
  asin TEXT, title TEXT, marketplace TEXT DEFAULT 'UK',
  category TEXT, variants_json TEXT,         -- 变体列表 JSON
  primary_variant TEXT,                      -- 主推变体
  status TEXT DEFAULT 'active',              -- active|archived
  created_at, updated_at )

-- product_profile_version：产品档案版本（SCD-2，PRD 4.2.A–F + 版本管理）
product_profile_version(
  id PK, product_id FK→product NOT NULL,
  version_no INTEGER NOT NULL,               -- 产品内自增
  payload_json TEXT NOT NULL,                -- {A基础,B经营,C定位,D市场,E广告,F特殊规则} 结构见 §1.3
  change_summary TEXT, change_reason TEXT NOT NULL,
  changed_by TEXT NOT NULL,                  -- 修改人（Gordon/运营）
  diff_json TEXT,                            -- 与上一版的差异
  effective_from TEXT NOT NULL,              -- 生效时间（=created_at）
  created_at,
  UNIQUE(product_id, version_no) )           -- 分析运行须引用 version_id（阶段二）

-- campaign_map：广告活动 → 产品 映射（事实层产品粒度汇总的前置）
campaign_map(
  id PK, campaign_id TEXT NOT NULL, campaign_name TEXT,
  marketplace TEXT DEFAULT 'UK', ad_product_type TEXT,   -- sp|sb|sd
  product_id FK→product, mapped_by TEXT NOT NULL,        -- 人工映射
  confidence TEXT DEFAULT 'confirmed', note TEXT,
  effective_from TEXT NOT NULL, created_at,
  UNIQUE(campaign_id, ad_product_type, effective_from) ) -- 历史映射可追溯
```

**事件域**

```sql
-- business_event：业务事件（PRD 4.3；阶段一手动记录，CLI 录入）
business_event(
  id PK, event_type TEXT NOT NULL,   -- deal|coupon|prime_discount|price_change|stock_change|
                                     -- out_of_stock|delivery_change|listing_change|image_change|
                                     -- competitor_change|seasonal|other
  product_id FK→product, asin TEXT,  -- 二者至少其一
  starts_at TEXT NOT NULL, ends_at TEXT,     -- ISO8601（含站点时区标注）
  description TEXT NOT NULL, data_source TEXT, recorded_by TEXT NOT NULL,
  impact_scope TEXT,                          -- 影响范围说明
  created_at )
```

**数据管道域（D11）**

```sql
-- import_batch：导入批次（PRD 4.1 处理要求）
import_batch(
  id PK, imported_at TEXT NOT NULL, imported_by TEXT NOT NULL,
  source_type TEXT NOT NULL,        -- manual_upload|agent_submit|mcp
  source_system TEXT NOT NULL,      -- amazon_console|lingxing|westmail|softtime|manual|...
  status TEXT NOT NULL DEFAULT 'draft',
    -- draft → identified → quality_running →
    --   完整(complete) | 部分完整(partial) | 不完整(incomplete) | 待确认(needs_confirm) | 校验失败(failed)
  summary_json TEXT,                -- 质检汇总（counts by severity/check_code）
  notes TEXT, created_at )

-- raw_dataset：原始文件登记（幂等锚点，PRD 4.1 数据保存）
raw_dataset(
  id PK, batch_id FK→import_batch NOT NULL,
  file_name TEXT NOT NULL, stored_path TEXT NOT NULL,   -- data/raw/<yyyy>/<batch短id>_<原名>
  file_hash TEXT NOT NULL UNIQUE,                       -- SHA-256（T1 幂等）
  file_size INTEGER, row_count INTEGER,
  report_type TEXT,                 -- campaign|adgroup|target|searchterm|placement|
                                    -- advertised_product|purchased_product|unknown
  ad_product_type TEXT,             -- sp|sb|sd|unknown
  marketplace TEXT, currency TEXT,
  attribution_window_days INTEGER,  -- 报表下载时选择的归因窗（7/14/30；从列名/下载选项推断）
  data_start_date TEXT, data_end_date TEXT,   -- 报表原生日期范围
  data_as_of TEXT NOT NULL,         -- 取数日（默认导入日；报表自带生成日期则用之）→ HR-01/02 计算基准
  status TEXT DEFAULT 'registered', -- registered|identified|normalized|superseded|rejected
  superseded_by TEXT,               -- 指向新版 raw_dataset.id（同范围新版本；旧数据不删）
  created_at )

-- report_identification：识别记录（可追溯、可人工纠正）
report_identification(
  id PK, raw_dataset_id FK NOT NULL,
  detected_type TEXT, confidence REAL,        -- 0~1
  matched_columns_json TEXT, missing_required_json TEXT, ambiguities_json TEXT,
  decided_by TEXT NOT NULL,                   -- auto|manual:<who>
  created_at )

-- quality_finding：质检明细（PRD 4.1 质量检查 → §4.2 检查项）
quality_finding(
  id PK, batch_id FK NOT NULL, raw_dataset_id FK,
  check_code TEXT NOT NULL, severity TEXT NOT NULL,  -- error|warn|info
  message TEXT NOT NULL, details_json TEXT, created_at )
```

**canonical 层（7 张同构表）**——以 campaign 粒度为模板，其余表追加业务键列：

```sql
can_campaign_daily(
  id PK, batch_id FK→import_batch NOT NULL, raw_dataset_id FK→raw_dataset NOT NULL,
  date TEXT NOT NULL,                      -- 报表原生日期（站点时区）
  marketplace TEXT NOT NULL, currency TEXT NOT NULL,
  ad_product_type TEXT NOT NULL,           -- sp|sb|sd（一表一行一类型，禁混）
  attribution_window_days INTEGER,
  campaign_id TEXT NOT NULL, campaign_name TEXT, campaign_status TEXT,
  portfolio_name TEXT,
  -- 度量（命名统一口径；原始多窗列全量保留在 attrs_json）
  impressions INTEGER, clicks INTEGER, spend REAL,
  orders INTEGER, sales REAL, units INTEGER,
  attrs_json TEXT,                         -- 源报表其余列原样（含 1d/7d/14d/30d 变体列）
  source_lineage_json TEXT,                -- {source_system, source_col_map} 列名映射留痕
  created_at,
  UNIQUE(batch_id, date, campaign_id) )

-- 其余 6 张在 can_campaign_daily 基础上追加：
--   can_adgroup_daily:            + adgroup_id, adgroup_name
--   can_target_daily:             + adgroup_id, adgroup_name, target_id, target_text, match_type
--   can_searchterm_daily:         + adgroup_id, adgroup_name, target_id, target_text, match_type, search_term
--   can_placement_daily:          + placement
--   can_advertised_product_daily: + adgroup_id, advertised_asin, advertised_sku
--   can_purchased_product_daily:  + adgroup_id, advertised_asin, advertised_sku, purchased_asin
-- 均 UNIQUE(batch_id, date, <业务键复合>)
```

**事实层**

```sql
-- metric_daily：事实指标快照（PRD 4.5；可复算 + 成熟度 + 可计算标记）
metric_daily(
  id PK, grain TEXT NOT NULL,               -- campaign|adgroup|target|searchterm|placement|product
  grain_keys_json TEXT NOT NULL,            -- {campaign_id, adgroup_id, ...}；product 粒度含 product_id
  product_id FK→product,                    -- 经 campaign_map 映射；未映射=NULL
  date TEXT NOT NULL, marketplace TEXT NOT NULL, currency TEXT NOT NULL,
  ad_product_type TEXT NOT NULL,
  -- 基础度量（自 canonical 汇总）
  impressions INTEGER, clicks INTEGER, spend REAL, orders INTEGER, sales REAL, units INTEGER,
  -- 派生指标（公式版本化，可复算）
  ctr REAL, cpc REAL, cvr REAL, acos REAL, roas REAL, cpa REAL,
  -- 预留（阶段一恒为 NULL + computable=false）：自然订单占比、tacos、gross_profit、ad_profit
  organic_order_ratio REAL, tacos REAL, gross_profit REAL, ad_profit REAL,
  computable_json TEXT NOT NULL,            -- {ctr:{computable:true}, cvr:{computable:false,reason:"zero_denominator"}, ...}
  maturity TEXT NOT NULL,                   -- mature|immature_observation（HR-01/02，按 data_as_of 回推）
  batch_refs_json TEXT NOT NULL,            -- 参与计算的 batch/raw_dataset 引用（T5 追溯）
  methodology_version_id FK→methodology_version NOT NULL,   -- 公式与成熟度规则版本
  formula_version TEXT NOT NULL, computed_at TEXT NOT NULL,
  UNIQUE(grain, grain_keys_hash(date+keys), date, ad_product_type, methodology_version_id) )

-- keyword_rank_snapshot：关键词自然位快照（PRD 4.4；阶段一手动/Agent 录入）
keyword_rank_snapshot(
  id PK, product_id FK→product NOT NULL, keyword TEXT NOT NULL,
  marketplace TEXT DEFAULT 'UK', captured_on TEXT NOT NULL,   -- 快照日
  organic_rank INTEGER, top_level INTEGER,                    -- 自然位（含大类排名可选）
  source TEXT NOT NULL,                -- manual|agent
  data_as_of TEXT, recorded_by TEXT, notes TEXT, created_at )
```

**方法论域（F1：权威链=Notion 页）**

```sql
-- methodology_version：方法论版本（PRD 4.6 版本管理）
methodology_version(
  id PK, version_no TEXT UNIQUE NOT NULL,     -- '1.0.0'
  title TEXT NOT NULL, status TEXT NOT NULL,  -- draft|released|deprecated
  source_type TEXT NOT NULL DEFAULT 'notion', -- F1：以 Notion 页为准
  source_uri TEXT,                             -- Notion 页 URL + last_edited
  file_hash TEXT NOT NULL,                     -- YAML 文件 SHA-256（防篡改）
  reviewed_by TEXT, approved_by TEXT, released_at TEXT,
  summary TEXT, created_at )

-- methodology_rule：规则行（YAML 解析入库；enforcement 供校验器/门禁索引）
methodology_rule(
  id PK, methodology_version_id FK NOT NULL,
  layer TEXT NOT NULL,             -- hard|param|knowledge
  rule_code TEXT NOT NULL,         -- HR-01 / target_acos / K-05 ...
  name TEXT, statement TEXT NOT NULL,
  params_json TEXT, enforcement TEXT,          -- ingest_gate|validator|acceptance|null
  source_json TEXT,                -- {section, blocks:[notion block-id]}
  created_at, UNIQUE(methodology_version_id, rule_code) )
```

**系统域**

```sql
-- audit_log：审计流水（PRD 7.3；append-only，触发器禁 UPDATE/DELETE）
audit_log(
  id PK, ts TEXT NOT NULL, actor TEXT NOT NULL,   -- human:<name> | agent:<harness> | cli
  action TEXT NOT NULL, object_type TEXT, object_id TEXT,
  details_json TEXT )
-- + 触发器：audit_log 自身及 raw_dataset/can_*/metric_daily 禁 UPDATE/DELETE（修正=新批次，D6/PRD 4.1）
```

### 1.3 产品档案 payload_json 结构（PRD 4.2 A–F）

```yaml
A_basic:        {asin, title, category, variants[], primary_variant}
B_operations:   {price, cost, unit_profit, profit_margin, organic_order_ratio,
                 inventory, business_goal, target_acos,           # ← F3：SKU 例外在此维护
                 exceptions: ["暂不停"]}                          # 如 6979 深灰
C_positioning:  {usage, target_users, selling_points[], compatible_with[],
                 related_products[], search_intents[], listing_summary}
D_market:       {competitor_asins[], core_keywords[], broad_keywords[],
                 own_ranks{}, competitor_ranks{}, competitor_changes[], focus_rivals[]}
E_advertising:  {main_campaigns[{campaign_id, campaign_name, ad_product_type}],  # ← campaign_map 初始来源
                 key_terms[], placement_notes{}, strategy_history[], past_actions[],
                 term_stages{}, term_restrictions{}}
F_special:      {protected_terms[], associated_traffic_allowlist[],
                 negative_restrictions[], bid_restrictions[], threshold_overrides{},
                 business_strategy}
```

### 1.4 关系图（ER 摘要）

```
product 1─n product_profile_version
product 1─n campaign_map (by campaign_id→product)
product 1─n business_event / keyword_rank_snapshot
import_batch 1─n raw_dataset 1─1 report_identification
import_batch 1─n quality_finding
raw_dataset 1─n can_*_daily (7 张) ──汇总──> metric_daily ──引用──> methodology_version
metric_daily n─1 product (经 campaign_map，可空)
```

---

## 2. 报表识别器设计

### 2.1 设计原则

- **数据驱动**：指纹配置独立于代码（`ads_core/ingest/fingerprints.yaml`，入 git）；真实样本到位后**只改配置不改代码**（Q3 决议的落地）
- **列名指纹，不依赖文件名**：归一化表头（去 BOM/首尾空白/全小写/去多余空格）后按「必要列组 + 区分列」打分
- **不确定即停**：置信度不足或前二名分差 < margin → `unknown + ambiguities`，等待 `--type` 人工指定（不猜测，对应 PRD「数据异常能够被发现，而不是被 AI 自行补齐」）

### 2.2 指纹种子（亚马逊控制台 bulk 下载·英文列名，待真实样本校准）

| report_type | 必要列（any-of 组） | 区分列（加分） |
|---|---|---|
| campaign | `date`; `campaign id`; 度量组 `impressions`+`clicks`+`spend` | `portfolio name`；**无** ad group 列 |
| adgroup | 上 + `ad group id` + `ad group name` | — |
| target | 上 + (`keyword or product targeting` 或 `targeting`) + `match type` | `keyword id` |
| searchterm | 上 + `customer search term` | `keyword or product targeting` |
| placement | `date`; `campaign id`; `placement` | 度量组 |
| advertised_product | `date`; `advertised asin`（或 `asin`）+ `advertised sku` | `ad group id` |
| purchased_product | `date`; `advertised asin` + (`purchased asin` 或 `other asin`) | — |

度量列别名（any-of）：`spend`/`cost`、`orders`/`7 day total orders (#)`、`sales`/`7 day total sales`、`units`/`7 day total units (#)`。归因窗从列名后缀（`7 day`/`14 day`/`30 day`）推断并写入 `raw_dataset.attribution_window_days`；多窗共存时取报表主窗并全量保留 `attrs_json`。

- **ad_product_type（sp/sb/sd）**：控制台按类型分别下载，文件内容不总自带标识；识别器给 `unknown` 时由 `--ad-product-type` 指定，默认建议 SP。SB/SD 报表结构差异大，**阶段一种子只覆盖 SP 全类型 + campaign 通用骨架**，SB/SD 待真实样本补充（开放问题 O-2）
- **XLSX/CSV** 均支持；CSV 按 `utf-8-sig` 读取（R7）

### 2.3 识别输出

`detect(headers) → {detected_type, confidence, matched, missing_required, ambiguities}`；confidence = 必要组命中比例 × 0.8 + 区分列命中 × 0.2；`< 0.85` 或 top1−top2 `< 0.1` → 人工确认。

---

## 3. 数据管道设计（PRD 5.1 步骤 1–5 的落地）

```
文件/数据 ──ingest──► raw_dataset(+report_identification)
      │                    │
      │              文件级质检（QF-01~06, 08）
      ▼                    ▼
  批次状态机 ──pass──► 源适配器（amazon_console v1）──► can_*_daily
      │                                            │
      │ 不完整/待确认 → 停（PRD 5.1：不得跳过）      跨源/跨批质检（QF-07, 09, 10）
      ▼                                            ▼
                                            事实构建 facts（metric_daily + maturity）
```

### 3.1 批次状态机

`draft → identified → quality_running → {complete | partial | incomplete | needs_confirm | failed}`
（PRD 4.1 五态枚举一一对应：完整/部分完整/不完整/待确认/校验失败）
规则：存在 `error` 级 finding → `failed` 或 `incomplete`；仅 `warn` → `needs_confirm`（人工 `ads batch confirm` 后按内容归 `complete/partial`）；`incomplete/needs_confirm/failed` 批次**不进入 canonical 与 facts**（PRD：「不完整或关键冲突的数据不允许进入正式决策流程」）。

### 3.2 质检项清单（quality_finding.check_code）

| 码 | 检查 | 级别 | 对应测试 |
|---|---|---|---|
| QF-01 | 必要字段缺失（识别器必要列在数据体中存在空列/丢列） | error | T1 |
| QF-02 | 批内重复记录（同业务键同行，值不一致更升 error） | warn/error | T1 |
| QF-03 | 格式异常（数值列非数值、日期解析失败、负值） | error | T1 |
| QF-04 | 日期范围不完整（gap 检测、起止与文件名/预期窗不符） | warn | T3 |
| QF-05 | 币种不一致（批内多币种 / 与 marketplace 默认 GBP 不符） | error | T1/T2 |
| QF-06 | 统计口径不一致（同批混归因窗 / 混广告类型） | error | T2/T3 |
| QF-07 | 数据窗口不匹配（与既有批次重叠且同键值不同 → 标记待确认） | warn | T1 |
| QF-08 | 同报表新版本（同类型+同日期范围、不同 hash → supersede 流程，旧数据保留） | warn | T1 |
| QF-09 | 跨源冲突（amazon vs 领星同键同日差异超容差，默认 1%） | error | T1 |
| QF-10 | 成熟度概览（未成熟天数占比，info；不阻断） | info | T3 |

---

## 4. 事实指标计算设计（PRD 4.5 × 方法论 v1.0.0）

### 4.1 公式与口径（`formula_version = methodology.version_no`）

| 指标 | 公式 | 分母为零/缺源 → computable=false reason |
|---|---|---|
| CTR | clicks / impressions | `zero_impressions` |
| CPC | spend / clicks | `zero_clicks` |
| CVR | orders / clicks | `zero_clicks`（HR-10：未成熟日不进加权——加权是阶段二分析侧职责，事实层只标记） |
| ACOS | spend / sales | `zero_sales` |
| ROAS | sales / spend | `zero_spend` |
| CPA | spend / orders | `zero_orders` |
| organic_order_ratio / TACoS / gross_profit / ad_profit | 需业务订单、毛利数据源 | 阶段一恒 `missing_business_source`（**不估算**，PRD 4.5 红线） |

复算要求：`metric_daily` 全字段可由 `batch_refs_json` 指向的 canonical 行重算一致（T2 断言）。

### 4.2 成熟度标记（HR-01/02/04）

```
maturity(date, ad_product_type, data_as_of) =
  lag = data_as_of - date                    # 按日报表原生日期
  SP:  lag ≥ 7  → mature   否则 immature_observation
  SB/SD: lag ≥ 14 → mature 否则 immature_observation
  混合粒度（含 SB 行）→ 从严按 14 天（HR-02）
  T-2（data_as_of = today - 2）只挡进报表延迟，不改变上式（HR-04）
```

`metric_daily.maturity` 行级落库；查询侧约定：转化侧指标（orders/sales/acos/cvr）在 `immature_observation` 行**不进入**正式结论查询（阶段二分析上下文包强制过滤，本阶段提供 `ads facts --mature-only` 开关与占比统计）。

---

## 5. 模块与接口设计（ads_core）

```
ads_core/
├─ storage/         models.py(SQLAlchemy) · dao.py · audit.py · __init(rev: Alembic env)
├─ ingest/          fingerprints.yaml · detector.py · reader.py(xlsx/csv) · importer.py
├─ quality/         checks.py(QF-01~10) · batch_state.py(状态机)
├─ adapters/        base.py · amazon_console.py        # 领星等后续按 D11 增补
├─ canonical/       builder.py(can_* 写入+lineage)
├─ facts/           metrics.py(公式) · maturity.py · builder.py
├─ profile/         product.py · profile_version.py · campaign_map.py
├─ events/          business_event.py
├─ methodology/     loader.py(YAML→库) · params.py(运行时读参)
├─ cli.py           typer 入口（§6）
└─ pyproject.toml   依赖：sqlalchemy≥2.0, alembic, typer, openpyxl；dev: pytest, pyyaml
```

**Service 层关键签名（设计级，供评审）**

```python
ingest_file(path, source_system="amazon_console", source_type="manual_upload",
            imported_by, forced_type=None, ad_product_type=None) -> ImportResult
  # 含：hash 幂等检查(已存在→返回既有 batch, duplicate=True) → 识别 → 登记 → 文件级质检 → 状态机
build_canonical(batch_id) -> CanonicalResult        # 仅 complete/partial 批次
run_cross_checks(batch_id) -> list[Finding]         # QF-07/09（跨批/跨源）
build_facts(batch_id=None, date_range=None, grains=None) -> FactsResult
  # 从 canonical 汇总 → 公式计算 → 成熟度标记 → 全行落 metric_daily
methodology_import(yaml_path, expected_status="released") -> MethodologyVersion
upsert_product(...); update_profile(product_id, payload, reason, changed_by) -> ProfileVersion
add_business_event(...); add_keyword_rank(...)
```

审计：以上所有 service 入口统一写 `audit_log`（actor 透传）。

---

## 6. CLI 命令集（阶段一；全部支持 `--json`）

```
ads init                          # 建库+迁移+目录（data/raw, data/db）
ads methodology import <yaml>     # 导入方法论版本（校验 status=released、hash 落库）
ads methodology show [--latest]
ads ingest <file...> [--type T] [--ad-product-type sp|sb|sd] [--source-system S] [--by NAME]
ads batch list [--status S] / ads batch show <id> / ads batch confirm <id> [--as complete|partial]
ads quality rerun <batch>
ads canonical build <batch>
ads facts build [--batch id | --from D --to D] [--grains ...] [--mature-only]
ads facts maturity [--from D --to D]        # 未成熟占比统计
ads product add / ads profile update --file <yaml> --reason --by
ads event add --type --sku --from --to --desc --by
ads rank add --sku --kw --on --rank --by
ads audit tail [n]
```

命名对齐阶段五 MCP 工具：`ads_ingest_data`/`ads_validate_data`/`ads_build_facts` 将直接包装上述 service 函数（PRD 第十章）。

---

## 7. 测试设计（阶段一对应 T1/T2/T3 + T5 结构项）

pytest + 合成报表 fixtures（英文列名 XLSX/CSV，覆盖 7 类 SP 报表；不依赖真实数据，真实样本到位后另加校准用例）。

| 用例组 | 内容 | 断言要点 |
|---|---|---|
| test_t1_data_pollution | ① 同文件重复导入 ② 缺必要列 ③ 批内币种混 ④ 同报表改后重传 | ①返回同一 batch、无新行、`duplicate=true` ②`failed`+QF-01 ③error+状态阻断 ④supersede 生效、旧行完整保留、trace 链可查 |
| test_t2_metric_caliber | ① 混广告类型文件 ② 混归因窗 ③ 缺 spend 列 ④ 指标复算 | ①QF-06 拒绝入 canonical ②同上 ③acos 等标 `computable=false` ④全量重算与库内一致 |
| test_t3_maturity | 构造 data_as_of 近 7/14 天窗口数据 | SP<7 天行 `immature_observation`；含 SB 组从严 14 天；T-2 不改变成熟度；QF-10 info 生成 |
| test_t5_trace_struct | 事实行 → batch_refs → raw 文件 → hash | 全链可追溯（PRD 4.1） |
| test_methodology | YAML 导入、版本唯一、重复导入幂等、draft 拒绝发布 | 版本表/规则行完整 |
| test_profile_events | 档案版本化（改两次→3 版本+diff）、事件窗口查询 | SCD-2 语义正确 |

---

## 8. 工程规范

- Python **3.12 绝对路径**执行（工作区约定）；包管理 `venv + pip`（Git Bash 下不用 pipx run）
- Git：`data/`（raw+db）全量 ignore；`methodology/*.yaml`、`ads_core/ingest/fingerprints.yaml`、`profiles/` 入库；无密钥需求（阶段一零外呼，PRD 8.3）
- 迁移：Alembic `001_initial_stage1`（21 表 + 审计触发器）；后续每阶段增量迁移
- 每条 PRD 交付要求对应文档：README（运行方式）、`docs/stage1-delivery.md`（已完成/未实现/已知问题/测试结果，按 PRD 十一章模板）

---

## 9. 开放问题（不阻塞开发，按序解决）

| # | 问题 | 默认方案 |
|---|---|---|
| O-1 | 真实报表样本校准 fingerprints.yaml（Q3 遗留） | 首份真实报表到位后 1 个工作日内校准；识别失败不阻塞（人工 `--type` 兜底） |
| O-2 | SB/SD 报表结构种子缺失 | 阶段一覆盖 SP 全类型；SB/SD 到样即补种子（PRD 4.1：以首期实际使用为准） |
| O-3 | 归因窗下载选项默认值（7d/14d/30d） | 事实层优先取与广告类型成熟度一致的窗（SP 7d / SB·SD 14d），原始多窗列全保留 |
| O-4 | 6978/6979 的 campaign 清单（campaign_map + 档案 E 初始数据） | 开发期由 Gordon 提供（F4：新环境重建，不取 box） |
| O-5 | `data_as_of` 取值 | 报表自带生成日期 > 导入日；记录于 raw_dataset |

---

## 10. 阶段一交付物对照（PRD 十一章）

| 要求 | 交付 |
|---|---|
| 已完成功能 / 代码与目录结构 | `ads_core` 包 + CLI（§5/§6），README |
| 数据模型说明 | 本文 §1 + Alembic 迁移 |
| 核心业务流程说明 | 本文 §3 管道 + README 流程图 |
| 测试用例 / 运行结果 | §7 用例 + pytest 输出附 stage1-delivery.md |
| 已知问题 / 未实现功能 / 下阶段建议 | stage1-delivery.md（开发完成时产出，禁止空函数冒充完成） |
