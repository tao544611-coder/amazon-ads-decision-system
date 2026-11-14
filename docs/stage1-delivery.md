# 阶段一交付文档（按 PRD 十一章模板）

> 交付日期：2026-11-14 · 开发环境：本机（Python 3.12.10，venv 绝对路径执行）
> 上游依据：`stage1-detailed-design-v1.0.md`（实施蓝本）· `requirements-baseline.md`（PRD V1.0）

## 一、已完成功能

| 模块 | 内容 | 证据 |
|---|---|---|
| 数据模型 | 20 张业务表 + `alembic_version`，19 个不可变触发器（audit_log 禁改删；can_*/metric_daily 禁改删；raw_dataset 禁删） | 迁移 `001_initial_stage1`；建库验证 21 表 + 19 触发器 |
| 方法论导入 | YAML → `methodology_version`+`methodology_rule`（42 条 = 19 硬 + 9 参数 + 14 知识）；draft 拒绝、hash 不一致拒绝、同版幂等 | `test_methodology.py` 4 用例 |
| 报表识别器 | 列名指纹数据驱动（`fingerprints.yaml`）；归一化表头、置信度 0.85 / margin 0.1、不确定即停；归因窗推断 | `test_detector.py` 6 用例 + 真实样本校准 9 用例 |
| 导入管道 | file_hash 幂等（部分唯一索引：rejected 除外）、多文件共享批次（defer finalize）、文件落盘 `data/raw/<yyyy>/` | `test_t1_data_pollution.py` 5 用例 |
| 质检 QF-01~10 | 文件级（01/02/03/04/05）+ 批级口径（06）+ supersede（08）+ 跨批跨源（07/09，容差 1%）+ 成熟度概览（10） | 各测试组覆盖，见 §四 |
| 批次状态机 | 五态 `draft→identified→quality_running→{complete/partial/incomplete/needs_confirm/failed}`；error→failed、warn→needs_confirm 人工确认 | `batch_state.py` + T1/T2 用例 |
| canonical 层 | 7 张 `can_*_daily`（amazon_console v1 适配器：英文 SP 全类型 + SB campaign；区间型报表挂起始日+attrs 标注；lineage 列名映射留痕） | 真实样本 canonical 8287 行 |
| 事实层 | 6 粒度汇总（campaign/adgroup/target/searchterm/placement/product）；公式 ctr/cpc/cvr/acos/roas/cpa + computable 标记（分母零/缺源不估算）；成熟度行级标记（SP≥7/SB·SD≥14/混算从严/T-2 只挡延迟）；QF-10 | 真实样本 facts 8499 行；T2/T3 用例 |
| 产品域 | product、档案 SCD-2（版本号+diff）、campaign_map（历史可追溯，Id/活动名双键匹配）、业务事件、关键词排名 | `test_profile_events.py` 5 用例 + `test_facts_product_grain.py` |
| CLI | 15 命令全支持 `--json`（Q1 唯一接入面） | `ads --help`；全流程脚本跑通 |
| 审计 | 全 service 入口写 `audit_log`（append-only） | `ads audit` 可查 |

## 二、代码与目录结构

```
ads_core/ads_core/{storage,ingest,quality,adapters,canonical,facts,profile,events,methodology}/ + cli.py
ads_core/alembic/versions/001_initial_stage1.py
ads_core/tests/（9 个测试文件，38 用例）
scripts/rebuild_dev_db.sh（全流程演示/复现脚本）
profiles/6978_profile_v1.yaml、6979_profile_v1.yaml（O-4 初始档案）
```

## 三、数据模型与业务流程说明

见 `stage1-detailed-design-v1.0.md` §1/§3 与 `ads_core/README.md`（管道图）。

## 四、测试用例与运行结果

**运行命令**：`.venv/Scripts/python.exe -m pytest tests/ -q`
**结果：38 passed, 0 failed**（2026-11-14，本机）

| 组 | 用例数 | 覆盖 |
|---|---|---|
| test_t1_data_pollution | 5 | 重复导入幂等 / 缺列 failed / 混币种拒绝 / supersede 旧数据保留 / rejected 后重导 |
| test_t2_metric_caliber | 4 | 混广告类型 / 混归因窗（QF-06）/ 缺 spend → computable=false / 全量复算一致 |
| test_t3_maturity | 4 | SP 7 天阈值边界 / SB 14 天从严 / T-2 不改成熟度 / QF-10 |
| test_t5_trace_struct | 1 | facts → batch_refs → raw 文件 → hash 全链追溯 |
| test_methodology | 4 | 导入完整 / 幂等 / 篡改拒绝 / draft 拒绝 |
| test_profile_events | 5 | 档案 SCD-2 三版本+diff / campaign 映射 / 事件窗口 / 事件主体校验 / 排名快照 |
| test_detector | 5 | 7 类识别 / 垃圾表头 unknown / 缺列 unknown / 归因窗 |
| test_facts_product_grain | 1 | campaign_name 双键映射 → product 粒度出数 |
| test_calibration_real_samples | 9 | 真实样本表头（6 英文报表识别正确；领星中文×2+本地派生×1 必须 unknown） |

**真实样本全流程**（`scripts/rebuild_dev_db.sh`）：9 份样本 → 5 批 complete（8287 canonical 行）+ 1 批 failed（3 份不支持源被 QF-01 拦截）→ facts 8499 行（成熟 7856 / 未成熟 643 = 7.57%，符合 2026-11-12 单日报表距取数日不足 7 天的事实）。

## 五、已知问题

1. **样本报表不含 6978/6979 活动**（采集报告已证）：campaign_map 6 条映射（W46 facts 提取）在当前真实数据下 product 粒度出 0 行——映射逻辑已由合成测试验证，待含 6978/6979 的报表到位即出数。
2. **共用活动 SKU 归属口径待 Gordon 确认**：6978_6979 组 7 个 SP 活动共用（W46 facts 两 SKU 均有花费行），现统一挂 6978；拆分需 advertised_product 粒度数据（阶段二）。
3. **领星中文源未适配**（D11 后续增补）：关键词/广告位中文报表按设计拒绝入 canonical，登记留档。
4. **区间型报表挂起始日**：Campaign 区间汇总（Start Date/End Date）以起始日进 `can_campaign_daily`，attrs 记 `__report_granularity=range`；daily 语义按逐日报表为准。
5. **W46 facts 中 1 个零花费活动被折叠未显示实名**（G-6），campaign_map 暂建 6 条，第 7 条待实名补充；SB 活动无实名（台账「不再追实名」），仅入档案 E 组。

## 六、对设计文档的实现偏差（全部已在代码注释标注）

| # | 偏差 | 理由 |
|---|---|---|
| 1 | `raw_dataset` 禁 UPDATE 改为仅禁 DELETE | QF-08 supersede 需更新过程字段 `status/superseded_by`（设计自身要求） |
| 2 | 无区分列类型（placement/purchased_product）置信区分项视为满足 | 设计 §2.3 公式下该类满分恒 0.8 < 0.85，永远 unknown（公式缺陷） |
| 3 | `file_hash` 唯一改为部分唯一索引（rejected 除外） | 误参数批次失败后同文件无法重导（真实冒烟发现） |
| 4 | canonical `campaign_id` 装活动名（后台报表无 Id 列时）+ QF-02 键含 target/SKU 维 + can_advertised/purchased UNIQUE 含 SKU | 真实样本校准（O-1），lineage 记录实际来源列 |
| 5 | facts 汇总初始值 None 而非 0 | 缺源列不得虚构 0（PRD 4.5 红线，真实测试发现） |
| 6 | xlsx 读取用完整模式（非 read_only） | 后台/WPS xlsx dimension 元数据损坏，read_only 误判 1x1（采集报告备注 5 已预警） |

## 七、未实现功能（阶段一范围外）

- `analysis_run/evidence`（阶段二）、proposal→experience 链（阶段三）、竞品快照（阶段四）
- organic_order_ratio/TACoS/gross_profit/ad_profit：恒 `missing_business_source`（需业务订单/毛利数据源，PRD 4.5 红线：不估算）
- SB/SD 非 campaign 粒度报表种子（无样本，O-2 剩余部分）；领星适配器（D11）

## 八、下阶段入口

- 阶段二（分析与建议）：`analysis_run`+`evidence` 表、上下文包生成、建议校验器（PRD 第十章）
- 待 Gordon 决策：共用活动 SKU 拆分口径；第 7 个活动实名与 SB 活动实名补录
