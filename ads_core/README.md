# ads_core · 亚马逊广告智能优化与决策系统（阶段一核心引擎）

本地运行的独立业务引擎：数据导入 → 识别 → 质检 → 标准化（canonical）→ 事实指标。
Python ≥3.12 + SQLite(WAL) + 文件系统；**核心引擎零 LLM 依赖**（AI 在引擎外，经 CLI `--json` 接入）。

## 快速开始

```bash
cd ads_core
.venv/Scripts/python.exe -m pip install -e .[dev]   # 已装可跳过
.venv/Scripts/ads.exe init                           # 建库+迁移+目录
.venv/Scripts/ads.exe methodology import ../methodology/v1.0.0.yaml
.venv/Scripts/ads.exe ingest <报表文件...> [--type T] [--ad-product-type sp|sb|sd] --by <name>
```

完整演示流程见 `../scripts/rebuild_dev_db.sh`（init → 方法论 → 产品档案 → 映射 → 导入 → canonical → facts）。

## 数据管道（PRD 5.1）

```
文件 ──ingest──► raw_dataset(+report_identification)
 │                     │
 │               文件级质检 QF-01~05, 08        批次状态机
 ▼                     ▼                              │
 data/raw/<yyyy>/  批次状态机 ──complete/partial──► 源适配器 amazon_console v1
                          │                            │
              incomplete/needs_confirm/failed          ▼
                    （停，不进下游）            can_*_daily（7 张统一层表）
                                                       │ 跨批/跨源质检 QF-07, 09
                                                       ▼
                                              facts → metric_daily（含成熟度标记）
```

## CLI 命令（全部支持 `--json`）

| 命令 | 用途 |
|---|---|
| `ads init` | 建库+迁移+目录 |
| `ads methodology import <yaml>` / `show [--latest]` | 方法论版本管理（draft 拒绝、hash 防篡改） |
| `ads ingest <file...>` | 导入报表（file_hash 幂等；多文件共享批次） |
| `ads batch list/show/confirm` | 批次管理（needs_confirm 人工确认） |
| `ads quality rerun <batch>` | 重跑质检 |
| `ads canonical build <batch>` | 构建统一层（仅 complete/partial） |
| `ads facts build [--batch/--from/--to/--grains/--mature-only]` | 构建事实指标 |
| `ads facts maturity [--from/--to]` | 未成熟占比统计（QF-10） |
| `ads product add / profile-update / map-campaign` | 产品、档案 SCD-2、活动映射 |
| `ads event add / rank-add` | 业务事件、关键词排名快照 |
| `ads audit [n]` | 审计流水 |

## 目录

```
ads_core/
├─ ads_core/            # 包：storage / ingest / quality / adapters / canonical / facts / profile / events / methodology / cli
├─ alembic/             # 迁移（001_initial_stage1：20 业务表 + alembic_version + 19 不可变触发器）
├─ tests/               # pytest（T1/T2/T3/T5 + methodology + profile + detector + 真实样本校准）
├─ alembic.ini / pyproject.toml
```

## 约定

- `ADS_HOME` 环境变量可覆盖根目录（默认仓库根）；DB=`data/db/ads.sqlite`，原始文件=`data/raw/<yyyy>/`
- `data/`、`samples/` 全量 gitignore（真实经营数据仅本地）
- 测试：`.venv/Scripts/python.exe -m pytest tests/ -q`
