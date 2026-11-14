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

**阶段一、二、三已交付**（2026-11-14，本设备）：

- **阶段一**：业务引擎 `ads_core/`（数据管道 + QF-01~10 + canonical + 事实指标 + 档案 + CLI）；真实样本全流程（canonical 8287 行 / facts 8499 行）
- **阶段二**：`analysis_run`+`evidence` + 上下文组装器（PRD 4.7 七步，D3 零 LLM）+ 建议校验器三关（VR-01~06 / EV-01/02，D4）
- **阶段三**：决策链 7 表 + FTS5（003 迁移）——提案→审批（审批与执行分离 T6，modify 终稿保存）→动作（幂等键 T8，simulated/manual）→回执（unknown→state_unknown 禁盲目重执行）→观察（F5 窗口=max(验证窗,归因满窗)，同期干扰自动拉取 T7）→验收（四裁决+归因分层，干扰未排除禁强行判定）→回滚（留痕不删）→经验库（中文 FTS 检索，失败经验保留，上下文接入 related_experience）
- 测试 **64/64 全绿**（一 38 + 二 14 + 三 12；T1–T8 引擎侧全覆盖）
- 真实库冒烟：失败建议不入提案 ✓、真实经验 EXP-2026-11-14-001 沉淀+中文检索命中 ✓
- 活动归属口径已落地（前缀规则=后台 SKU 前缀+KNGT，GJHIUBKNGT→6978，档案 v2）

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
| **`docs/stage2-detailed-design-v1.0.md`** | **阶段二实施蓝本**（2 表、七步组装、VR-01~06、T4/T5 用例） |
| **`docs/stage2-delivery.md`** | **阶段二交付文档** |
| **`docs/stage3-detailed-design-v1.0.md`** | **阶段三实施蓝本**（7 表、决策链状态机、守卫 G1–G6、FTS5） |
| **`docs/stage3-delivery.md`** | **阶段三交付文档** |
| `ads_core/README.md` | 运行方式 + 管道图 + CLI 表 |
| `ads_core/` | 引擎代码 + alembic 迁移 + tests（9 文件 38 用例） |
| `scripts/rebuild_dev_db.sh` | 全流程演示/复现脚本（init→方法论→档案→映射→导入→canonical→facts） |
| `profiles/6978_profile_v1.yaml`、`6979_profile_v1.yaml` | O-4 初始档案 payload |
| `scripts/register_samples.py` | 样本登记脚本（哈希校验+复制，已执行 OK 32/32） |

## CHANGES（本次会话）

- 阶段一：从「设计定稿、0 行代码」到全量交付（偏差 6 处见 stage1-delivery §六）
- 活动归属口径：前缀规则落地（Gordon 指示），档案 v2
- 阶段二：设计→002 迁移+analysis 包+14 测试+冒烟
- 阶段三：设计→003 迁移+decision/experience 包+CLI 五组+12 测试+真实库冒烟（失败建议挡在提案外、经验沉淀与中文检索）

## NEXT_EXPECTED_STEP

1. **阶段四（真实业务验证）**：用 6978/6979 历史数据回归（前提：采集昕信报表，见下）→ 人工核对 AI 判断 → 记录 AI 错误案例（experience status=failed）→ 修正数据/规则问题 → 评估建议准确性；
2. **数据跟进（阻塞项）**：①采集含 6978/6979 活动的昕信报表（当前样本实为约德账号；上下文包 facts 为空 + data_gaps 标注是真实结果）②第 7 个零花费活动实名（W46 facts 折叠，若属 6978 应为 GJHIUBKNGT-* 前缀）与 SB 活动实名 ③SB/SD 其他粒度报表种子（O-2 剩余）④领星源适配器（D11）；
3. **阶段五（MCP 开放）**：fastmcp 包装 service 层（ads_ingest_data / ads_validate_data / ads_build_facts 等，PRD 十章）、读取/分析/提案分级权限、调用日志、观察窗自动调度（O3-2）；
4. **AI 侧接入试运行**：Harness 侧 AI 读 `ads analysis context --dump` → 按包内 proposal_schema 产建议 → `ads analysis validate` → `proposal submit/approve` 走完整决策链。

## CONSTRAINTS

（不变，原文重申）

1. **PRD 红线**：按阶段交付；所有功能须有实际运行结果或测试证据；禁止空函数/模拟返回值冒充完成；需求冲突先提问。
2. **方法论权威链**：`methodology/*.yaml` 版本化文件为准（当前 v1.0.0）；修改走「建议→人工审核→回归→发布新版本」（PRD 4.6）。
3. **架构铁律**：`ads_core` 零 LLM 依赖；CLI `--json` 唯一接入面（Q1）；事实层只从 canonical 构建（D11）；导入幂等不静默覆盖（D6）。
4. **口径**：UK/GBP；SKU 6978/6979；目标 ACOS 25%（SKU 例外归档案）；成熟度 SP≥7/SB·SD≥14/混算从严/T-2 只挡延迟。
5. **数据安全**：`data/` 与 `samples/` 全量 gitignore；真实经营数据仅本地；密钥不入库。
6. **环境**：本机 Python 3.12.10 = `C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe`；venv 位于 `ads_core/.venv`；Git Bash 下跑 CLI 注意 `python print` 输出带 `\r`（管道需 `tr -d '\r'`，见 rebuild 脚本）。
7. **验收**：T1–T8 见 PRD 第九章；T1/T2/T3 全绿+T5 结构项（阶段一）、T4/T5（阶段二）、T6/T7/T8（阶段三）——**64/64 全绿，引擎侧八项验收全覆盖** ✓。
