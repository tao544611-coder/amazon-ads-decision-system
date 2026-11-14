# HANDOFF — 亚马逊广告智能优化与决策系统（AD system）

> 最近更新：2026-11-14（五阶段引擎侧开发全部完成）· 本文件为跨会话/跨设备交接锚点
> 交接载体：本文件 + `docs/` 全部文档 + `methodology/v1.0.0.yaml` + `ads_core/`（代码）+ `samples/`（登记后样本，gitignore）

---

## GOAL

建设「亚马逊广告智能优化与决策系统」：本地运行的独立业务引擎（Python + SQLite + 文件系统），AI 只承担数据观察、业务分析和优化建议，人掌握关键决策权，通过真实执行结果持续完善广告优化方法论。

- 需求唯一基线：`docs/requirements-baseline.md`（PRD V1.0）
- 首期验证范围：SKU 6978 / 6979，UK 站（GBP）
- 项目当前位置：**PRD 五阶段引擎侧开发全部完成（78/78 测试全绿）；进入运营验证循环——等昕信 6978/6979 报表到位后按核对清单实操**

## STATUS

**五阶段引擎侧全部交付**（2026-11-14，本设备）：

| 阶段 | 交付 | 证据 |
|---|---|---|
| 一 | 数据管道：21 表+触发器、识别器（真实样本校准 O-1）、QF-01~10、canonical 7 表、事实指标+成熟度、档案 SCD-2 | stage1-delivery（canonical 8592 行勘误已核）/ facts 8499 行冒烟 |
| 二 | 分析：`analysis_run`+`evidence`、上下文组装器（4.7 七步）、建议校验三关（VR-01~06/EV-01/02） | stage2-delivery |
| 三 | 决策链：7 表+FTS5、提案→审批→动作（幂等）→回执→观察（F5 窗口+干扰拉取）→验收（四裁决）→回滚→经验库 | stage3-delivery |
| 四 | 验证支撑：真实样本回归（锚点固化）、评估统计、验收→经验草稿、人工核对清单 C-1~C-9 | stage4-delivery + stage4-verification-checklist |
| 五 | MCP 开放：`ads mcp serve`（stdio）、read/analysis/data 三级权限（9/13/21 工具，物理不注册）、调用留痕 | stage5-delivery |

- 测试 **78/78 全绿**（合成 64 + 真实样本回归 6 + MCP 8；T1–T8 引擎侧全覆盖）
- 活动归属口径：前缀规则=后台 SKU 前缀+KNGT（GJHIUBKNGT→6978，档案 v2 backend_skus 全量）

已批准决议链（不得回退）：Q1–Q3、F1–F5，见 `docs/architecture-review-v0.2.md` §0.1 与 `docs/methodology-extraction-diff.md` §五。

## EVIDENCE

| 文件 | 内容 |
|---|---|
| `docs/requirements-baseline.md` | PRD V1.0 全文（T1–T8 + 5 阶段） |
| `docs/architecture-review-v0.2.md` / `methodology-extraction-diff.md` | 架构 D1–D11、Q1–Q3、F1–F5 决议 |
| `methodology/v1.0.0.yaml` | 已发布规则源（权威链=Notion 页） |
| `docs/stage{1..5}-detailed-design-v1.0.md` | 各阶段实施蓝本 |
| `docs/stage{1..5}-delivery.md` | 各阶段交付文档（已完成/测试/已知问题/偏差） |
| `docs/stage4-verification-checklist.md` | Gordon 人工核对清单 C-1~C-9（运营验证入口） |
| `ads_core/` | 引擎代码 + 3 迁移 + tests（12 文件 78 用例）+ README |
| `scripts/rebuild_dev_db.sh` | 全流程演示/复现脚本 |
| `profiles/6978_profile_v2.yaml`、`6979_profile_v2.yaml` | 产品档案（含 backend_skus） |

## NEXT_EXPECTED_STEP

1. **运营验证循环（当前主线）**：①采集昕信 6978/6979 广告报表（阻塞项：当前样本实为约德账号，SKU 后缀 AtOZ1YODER 已证）②按 `stage4-verification-checklist.md` C-1~C-9 逐项核对并回填结果 ③周检实操走完整决策链（context→validate→submit→approve→action→observation→evaluation→experience draft）④累计 ≥5 条验收后 `ads evaluation stats` 出建议准确性
2. **数据跟进**：第 7 个零花费活动实名（若属 6978 应为 GJHIUBKNGT-* 前缀）与 SB 活动实名；SB/SD 其他粒度报表种子（O-2 剩余）；领星源适配器（D11）
3. **AI 侧接入**：Harness/其他 Agent 经 `ads mcp serve --permissions read,analysis` 接入（阶段五已开放）；或本机 CLI 直连
4. 方法论迭代：运营中发现规则问题 → 经验库沉淀 → 按 PRD 4.6 流程（建议→人工审核→回归→发布 v1.0.1+）走版本化

## CONSTRAINTS

1. **PRD 红线**：按阶段交付；所有功能须有实际运行结果或测试证据；禁止空函数/模拟返回值冒充完成；需求冲突先提问。
2. **方法论权威链**：`methodology/*.yaml` 版本化文件为准（当前 v1.0.0）；修改走「建议→人工审核→回归→发布新版本」（PRD 4.6）。
3. **架构铁律**：`ads_core` 零 LLM 依赖；CLI `--json` 与 MCP 为仅有的两个接入面（Q1/D1）；事实层只从 canonical 构建（D11）；导入幂等不静默覆盖（D6）；AI 在引擎外（D3）。
4. **口径**：UK/GBP；SKU 6978/6979；目标 ACOS 25%（SKU 例外归档案）；成熟度 SP≥7/SB·SD≥14/混算从严/T-2 只挡延迟（HR-01/02/04）。
5. **数据安全**：`data/`、`samples/`、`ads-system-base-samples_*/` 全量 gitignore；真实经营数据仅本地；密钥不入库不入日志不入上下文包。
6. **环境**：本机 Python 3.12.10 = `C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe`；venv=`ads_core/.venv`（含 fastmcp 4.2.0）；Git Bash 下 python print 带 `\r`（管道加 `tr -d '\r'`）；xlsx 后台缓存文件须完整模式读（reader 已处理）。
7. **验收**：T1–T8 引擎侧 78/78 全绿 ✓；运营侧验证按核对清单推进。
