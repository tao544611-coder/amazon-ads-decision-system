# HANDOFF — 亚马逊广告智能优化与决策系统（AD system）

> 最近更新：2026-11-14（阶段一开发完成）· 本文件为跨会话/跨设备交接锚点
> 交接载体：本文件 + `docs/` 全部文档 + `methodology/v1.0.0.yaml` + `ads_core/`（代码）+ `samples/`（登记后样本，gitignore）

---

## GOAL

建设「亚马逊广告智能优化与决策系统」：本地运行的独立业务引擎（Python + SQLite + 文件系统），AI 只承担数据观察、业务分析和优化建议，人掌握关键决策权，通过真实执行结果持续完善广告优化方法论。

- 需求唯一基线：`docs/requirements-baseline.md`（PRD V1.0 · 2026-11-13，Notion 全文存档）
- 首期验证范围：SKU 6978 / 6979，UK 站（GBP）
- 项目当前位置：**阶段一开发完成并全绿交付（T1/T2/T3 + T5 结构项，38 用例），真实样本全流程跑通；下一步阶段二**

## STATUS

**阶段一已交付**（2026-11-14，本设备）：

- 业务引擎 `ads_core/`：20 业务表 + 触发器（Alembic `001_initial_stage1`）、识别器（已按真实样本校准 O-1）、导入管道（file_hash 幂等 + 批次状态机 + QF-01~10）、canonical 7 表、事实指标（公式 + computable 标记 + 成熟度 HR-01/02/04）、产品档案 SCD-2、CLI 15 命令全 `--json`
- 测试 **38/38 全绿**（合成 fixtures，不依赖真实数据）；真实样本（9 份）全流程：5 批 complete → canonical 8287 行 → facts 8499 行（未成熟占比 7.57% 与数据事实一致）；领星中文 ×2 + 本地派生 ×1 被 QF-01 正确拦截
- 样本包已登记（32/32 哈希校验通过）：`samples/raw-reports/`（9 份）+ `samples/assets/`
- O-4 campaign 初始数据已建：6978/6979 档案 v1 + campaign_map 6 条（W46 facts 提取；组共用活动挂 6978 待 Gordon 确认拆分口径；第 7 个零花费活动与 SB 活动无实名未建）

已批准决议链（不得回退）：Q1–Q3、F1–F5 同前，见 `docs/architecture-review-v0.2.md` §0.1 与 `docs/methodology-extraction-diff.md` §五。

## EVIDENCE

| 文件 | 内容 |
|---|---|
| `docs/requirements-baseline.md` | PRD V1.0 全文（T1–T8 测试标准 + 5 阶段规划） |
| `docs/architecture-review-v0.2.md` | 架构评审（D1–D11、Q1–Q3） |
| `docs/methodology-extraction-diff.md` | 方法论提取对照 + F1–F5 决议 |
| `methodology/v1.0.0.yaml` | 已发布规则源 v1.0.0（status: released，权威链=Notion 页） |
| `docs/stage1-detailed-design-v1.0.md` | 阶段一实施蓝本（21 表、识别器、状态机、QF、公式、CLI） |
| **`docs/stage1-delivery.md`** | **阶段一交付文档**（已完成/测试结果/已知问题/实现偏差/未实现） |
| `ads_core/README.md` | 运行方式 + 管道图 + CLI 表 |
| `ads_core/` | 引擎代码 + alembic 迁移 + tests（9 文件 38 用例） |
| `scripts/rebuild_dev_db.sh` | 全流程演示/复现脚本（init→方法论→档案→映射→导入→canonical→facts） |
| `profiles/6978_profile_v1.yaml`、`6979_profile_v1.yaml` | O-4 初始档案 payload |
| `scripts/register_samples.py` | 样本登记脚本（哈希校验+复制，已执行 OK 32/32） |

## CHANGES（本次会话）

从「设计定稿、0 行代码」推进到阶段一交付：全部代码/迁移/测试/文档/真实样本冒烟/O-1 校准/O-4 初始数据。实现与设计的 6 处偏差全部在 `docs/stage1-delivery.md` §六列明（触发器放行范围、识别器公式修正、部分唯一索引、canonical 键装活动名、None 不虚构 0、xlsx 完整模式）。

## NEXT_EXPECTED_STEP

1. **阶段二开发**（按 PRD 第十章）：`analysis_run` + `evidence` 表迁移 → 分析上下文包生成（成熟度过滤 HR-01/02 强制）→ 建议校验器（方法论 enforcement=validator 的硬规则）→ T4 对应测试；
2. **待 Gordon 决策**：①6978_6979 共用活动的 SKU 拆分口径（现挂 6978）②第 7 个零花费活动实名 ③SB 活动实名补录；
3. **数据跟进**：采集含 6978/6979 活动的报表（当前样本不含，product 粒度出 0 行是真实结果非缺陷）；SB/SD 其他粒度报表到样即补指纹种子（O-2 剩余）；领星源适配器（D11）。

## CONSTRAINTS

（不变，原文重申）

1. **PRD 红线**：按阶段交付；所有功能须有实际运行结果或测试证据；禁止空函数/模拟返回值冒充完成；需求冲突先提问。
2. **方法论权威链**：`methodology/*.yaml` 版本化文件为准（当前 v1.0.0）；修改走「建议→人工审核→回归→发布新版本」（PRD 4.6）。
3. **架构铁律**：`ads_core` 零 LLM 依赖；CLI `--json` 唯一接入面（Q1）；事实层只从 canonical 构建（D11）；导入幂等不静默覆盖（D6）。
4. **口径**：UK/GBP；SKU 6978/6979；目标 ACOS 25%（SKU 例外归档案）；成熟度 SP≥7/SB·SD≥14/混算从严/T-2 只挡延迟。
5. **数据安全**：`data/` 与 `samples/` 全量 gitignore；真实经营数据仅本地；密钥不入库。
6. **环境**：本机 Python 3.12.10 = `C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe`；venv 位于 `ads_core/.venv`；Git Bash 下跑 CLI 注意 `python print` 输出带 `\r`（管道需 `tr -d '\r'`，见 rebuild 脚本）。
7. **验收**：T1–T8 见 PRD 第九章；阶段一 T1/T2/T3 全绿 + T5 结构项 ✓。
