# Amazon Ads Decision System（亚马逊广告智能优化与决策系统）

版本 **v0.1** ｜ Python 3.12 ｜ SQLite + Alembic ｜ 测试 89 用例全绿

一套**引擎确定性、判断可追溯**的亚马逊广告决策链系统：数据采集 → 统一层 → 事实层 → 分析上下文 → AI 建议 → 三关校验 → 人工审批 → 执行回执 → 观察验收 → 经验沉淀。AI 只出建议，引擎管事实与护栏，**每一笔改动都可从建议追溯到执行回执**。

![架构图](docs/architecture.svg)

## 架构要点

- **三段管道（D11 决议）**：源报表 → 源适配器 → canonical 统一层 → 事实层。事实层禁止读源报表；新增数据源只写适配器，下游零改动。当前适配器：`amazon_console`（后台 bulk 报表）、`lingxing_erp`（领星中文周报）。
- **AI 在引擎外（D3）**：`ads_core` 零 LLM 依赖。引擎只做确定性计算（成熟度、口径、质检、护栏），AI 通过冻结的**分析上下文包**读取事实并产出建议。
- **建议三关校验（D4）**：Schema14 必填字段 → VR-01~06 硬规则（未成熟行转化指标、调价幅度 ±15%、出价上下沿 £0.10~2.00、点击样本 ≥20、干扰事件引用）→ EV-01/02 证据存在性。不过关的建议进不了提案表。
- **审批与执行分离（T6）**：Web 审批门（`webgate/`，本机 127.0.0.1:8765）人工四决策（批准/修改批准/驳回/暂缓）；批准后另行生成动作，由 AI 会话经领星 MCP 执行并回执，执行前有对象核对硬闸。
- **两个接入面（Q1/D1）**：CLI（`--json`）与 MCP Server（三级权限 9/13/21 个工具，物理不注册=最硬边界）。
- **质检体系（QF-01~10）**：报表类型指纹识别（含中文变体映射）、必要列/日期/数值/重复业务键/口径一致、跨批 QF-07（同源 warn）/QF-09（跨源 error）。

## 目录

```
ads_core/      引擎包：ingest（识别/导入）→ adapters（源适配器）→ canonical → facts（成熟度/口径）
               analysis（上下文组装/三关校验）→ decision（提案/审批/动作/回执/观察/验收）
               experience（经验库 FTS5）+ methodology（版本化规则源）+ mcp_server
webgate/       审批门 Web 页（Starlette + 原生 JS 单页，纯展示层零 SQL）
docs/          各阶段设计稿、交付文档、验收清单、执行 SOP、领星工具需求单
methodology/   已发布规则源 v1.0.0（19 条硬规则 HR-01~19 + 参数组）
scripts/       样本登记、开发库重建、批量导入等运维脚本
```

## 快速开始

```bash
cd ads_core
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # Windows
.venv/Scripts/ads init                                          # 建库 + 迁移
.venv/Scripts/ads methodology import ../methodology/v1.0.0.yaml # 导入规则源
.venv/Scripts/ads ingest <报表文件> --source-system amazon_console
.venv/Scripts/ads canonical build <batch_id>
.venv/Scripts/ads facts build --date-from 2026-10-01 --date-to 2026-10-31
.venv/Scripts/ads analysis context --sku <SKU> --date-from ... --date-to ...
.venv/Scripts/ads analysis validate <run_id> --file 建议.json   # 三关校验
.venv/Scripts/ads proposal submit <run_id>                      # 迁入提案（pending_approval）
```

审批门：双击 `审批门.bat`（或 `python webgate/app.py`），浏览器自动打开 `http://127.0.0.1:8765`。

MCP 接入：`ads mcp serve --permissions read,analysis`（三级 read/analysis/data，工具物理不注册）。

运行测试：`cd ads_core && .venv/Scripts/python -m pytest tests/ -q`（89 用例）。

## 数据与隐私

- **真实经营数据不入库**：`data/`、`samples/`、样本包目录全量 gitignore，密钥永不进代码/提交/日志。
- 仓库内文档与测试已脱敏：真实店铺别名、领星 sid/profile、ASIN、活动 ID 均替换为占位值（`B0TEST****`、`TESTNGT-*`、`111111…`）。
- 产品档案（profiles/）与本地交接笔记（HANDOFF.md）含经营细节，仅存本地不入库。

## 版本

- **v0.1**（2026-11-14）：五阶段引擎交付——T1~T8 引擎侧 78 用例 + D11 一期（领星 ERP 适配 / MCP 补充证据）+ Web 审批门 + 领星执行通道实测通过，累计 89 用例。
