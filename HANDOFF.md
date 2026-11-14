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

**阶段一、阶段二已交付**（2026-11-14，本设备）：

- **阶段一**：业务引擎 `ads_core/`（21 表 + 触发器、识别器已校准 O-1、导入管道幂等 + QF-01~10、canonical 7 表、事实指标 + 成熟度、档案 SCD-2、CLI）；真实样本全流程 5 批 complete → canonical 8287 行 → facts 8499 行
- **阶段二**：`analysis_run`+`evidence` 表（002 迁移）、上下文组装器（PRD 4.7 七步 · D3 零 LLM）、建议校验器三关（Schema 14 字段 → 硬规则 VR-01~06 → 证据存在性 EV-01/02，D4）、CLI `ads analysis` 3 命令；真实库端到端冒烟（context → 伪造证据建议被 EV-01 拒）
- 测试 **52/52 全绿**（阶段一 38 + 阶段二 14）
- 活动归属口径已落地（2026-11-14 Gordon 指示）：前缀规则 = 后台 SKU 前缀 + KNGT；GJHIUBKNGT-* → 6978（档案 v2 已补 backend_skus 全量）

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
| `ads_core/README.md` | 运行方式 + 管道图 + CLI 表 |
| `ads_core/` | 引擎代码 + alembic 迁移 + tests（9 文件 38 用例） |
| `scripts/rebuild_dev_db.sh` | 全流程演示/复现脚本（init→方法论→档案→映射→导入→canonical→facts） |
| `profiles/6978_profile_v1.yaml`、`6979_profile_v1.yaml` | O-4 初始档案 payload |
| `scripts/register_samples.py` | 样本登记脚本（哈希校验+复制，已执行 OK 32/32） |

## CHANGES（本次会话）

- 阶段一：从「设计定稿、0 行代码」到全量交付（代码/迁移/测试/文档/真实样本冒烟/O-1 校准/O-4 初始数据）；6 处设计实现偏差见 `docs/stage1-delivery.md` §六
- 活动归属口径：前缀规则落地（Gordon 指示，映射表依据），档案 v2 + campaign_map note 更新
- 阶段二：`stage2-detailed-design-v1.0.md` 设计 → 002 迁移 + analysis 包（context/validator）+ CLI + 14 测试 + 交付文档 + 真实库端到端冒烟

## NEXT_EXPECTED_STEP

1. **阶段三开发**（审批与观察闭环，PRD 第十章）：`proposal`+`approval`+`action`+`execution_receipt`+`observation`+`evaluation`+`experience` 七表迁移 → 提案状态机（`proposals_json` 通过项迁入正式表）→ 观察窗口状态机（F5：观察窗=max(验证窗, 归因满窗)）→ 回滚流程（T8）→ 经验库 FTS5 → T6/T7/T8 测试；
2. **数据跟进**：①第 7 个零花费活动实名（W46 facts 折叠；若属 6978 按前缀规则应为 GJHIUBKNGT-*）与 SB 活动实名待后台核实 ②采集含 6978/6979 活动的昕信报表（当前样本实为约德账号，SKU 后缀 AtOZ1YODER 证实；上下文包 facts 区为空 + data_gaps 标注是真实结果）③SB/SD 其他粒度报表到样即补指纹种子（O-2 剩余）④领星源适配器（D11）；
3. **AI 侧接入**：Harness 侧 AI 读 `ads analysis context --dump` 输出 → 按包内 proposal_schema 产建议 → `ads analysis validate` 提交（D3 闭环，可开始试运行）。

## CONSTRAINTS

（不变，原文重申）

1. **PRD 红线**：按阶段交付；所有功能须有实际运行结果或测试证据；禁止空函数/模拟返回值冒充完成；需求冲突先提问。
2. **方法论权威链**：`methodology/*.yaml` 版本化文件为准（当前 v1.0.0）；修改走「建议→人工审核→回归→发布新版本」（PRD 4.6）。
3. **架构铁律**：`ads_core` 零 LLM 依赖；CLI `--json` 唯一接入面（Q1）；事实层只从 canonical 构建（D11）；导入幂等不静默覆盖（D6）。
4. **口径**：UK/GBP；SKU 6978/6979；目标 ACOS 25%（SKU 例外归档案）；成熟度 SP≥7/SB·SD≥14/混算从严/T-2 只挡延迟。
5. **数据安全**：`data/` 与 `samples/` 全量 gitignore；真实经营数据仅本地；密钥不入库。
6. **环境**：本机 Python 3.12.10 = `C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe`；venv 位于 `ads_core/.venv`；Git Bash 下跑 CLI 注意 `python print` 输出带 `\r`（管道需 `tr -d '\r'`，见 rebuild 脚本）。
7. **验收**：T1–T8 见 PRD 第九章；阶段一 T1/T2/T3 全绿 + T5 结构项 ✓；阶段二 T4/T5 全绿（52/52）✓。
