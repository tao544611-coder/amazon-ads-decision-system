# Agent 提示词 · 工作设备端采集基础样本包

> 用途：采集广告报表样本 + 检测方法论外部资产，打包为基础样本包。
> **状态（2026-11-14）：采集任务已由本设备另一 agent 执行中，本文件勿重复发起采集**；对接收端开发 agent 的价值在文末「样本包回来之后的登记约定」（样本到位后按此校验入库）。
> 来源：2026-11-14 会话生成，配合 HANDOFF.md 使用。

---

```text
任务：采集「亚马逊广告系统基础样本包」并检测本机方法论外部资产（只读复制，不改动任何原文件）

## 背景
本机（另一台工作设备）可能有亚马逊广告业务的真实报表和一套广告方法论工作资产。
需要复制一份样本包出来，用于在另一台设备上搭建「亚马逊广告智能优化与决策系统」的基础数据样本。
你不改变本机任何文件，只做：检测 → 复制 → 打包 → 报告。

## 任务一：检测方法论外部资产（存在则复制，不存在如实记录 NOT FOUND）
按下列清单逐项查找（路径可能不完整，按文件名/关键词全盘搜索常见工作区：
/workspace、~/、桌面、下载、D:\ 等）：
1. 广告方法论主文件：广告投放分析方法论_规则_2026-10-26.md（预期在某 rubick-ai-org/reviews/ 下）
2. 方法论补丁稿：广告投放分析方法论_补丁稿_归因成熟度_2026-10-30.md、广告方法论_§3.3待批补丁_2026-10-26.md
3. 周检事实表目录：reviews/ads-weekly-audit/facts/ 整目录（*.md 事实文件）
4. 词级处置复盘：2026-10-26_Gordon6978词级处置_思路与W44复盘点.md（reviews/ads-weekly-audit/ 下）
5. 白名单分组文件：products.md（含分组 6384 / 6978_6979）
6. 行动验证台账：广告行动验证台账_昕信.md（预期 products/ads-check/ 下）
7. 规程链文件：chains/fact-then-judge.md、chains/file-landing.md
8. 算价脚本包：apollo-ai-bid-skill/（含 scripts/，只要代码与说明，不要运行）
9. ads-bid-scripts/（若存在）
10. Notion 存放规则：Notion_AmazonWorkSpace_存放规则_草案_2026-10-20.md
搜索关键词：广告投放分析方法论、ads-weekly-audit、行动验证台账、apollo-ai-bid-skill、fact-then-judge、products.md。

## 任务二：采集广告报表数据样本（优先 UK 站、SKU 6978 / 6979 相关）
搜索下载/桌面/导出目录中的亚马逊广告报表（xlsx/csv，也留意 bulk JSON 快照与领星 lingxing 导出）。
目标覆盖以下报表类型，每类取 1–2 份最近的（优先含 6978/6979、优先不同日期范围）：
Campaigns、Ad Groups、Keywords/Targeting、Search Terms、Placement、Advertised Products、Purchased Products。
每份样本必须记录：原路径、文件名、推测报表类型、数据日期范围（打开文件看 Date 列）、
归因窗（列名含 7 Day/14 Day/30 Day 则记下）、文件大小、SHA-256、来源（后台下载/领星导出/未知）。
找不到的类型在报告里写 NOT FOUND，不要用近似文件冒充。

## 打包结构（输出到桌面，文件夹+zip 同名）
ads-system-base-samples_YYYYMMDD/
├─ manifest.json   # 全部条目：原路径/类型/日期范围/归因窗/hash/大小/来源；资产检测结果 found/missing 分列
├─ reports/        # 报表样本，保留原始文件名
├─ assets/         # 方法论资产副本，保留原相对目录结构（如 reviews/ads-weekly-audit/facts/...）
└─ SUMMARY.md      # 检测+采集报告：逐项 found/NOT FOUND、样本覆盖了哪些报表类型、异常与备注

## 约束
- 全程只读：只复制，不修改/移动/删除原文件，不运行任何脚本包里的代码。
- 报表与资产含真实经营数据：仅在本机处理与打包，不得上传任何云端或第三方服务。
- zip 总体积尽量控制在 100MB 内（facts/报表优先，超大文件记录路径与 hash 后跳过并注明）。
- 完成后输出：zip 绝对路径 + SUMMARY.md 摘要（找到几项、缺几项、报表样本多少份）。
```

---

## 样本包回来之后的登记约定（接收端执行）

1. 校验 `manifest.json` 中每条 SHA-256；
2. 报表样本 → `samples/raw-reports/`（保留原文件名，导入系统时哈希直接作为 `raw_dataset.file_hash` 幂等键）；
3. 方法论资产 → `samples/assets/`（保留相对目录结构；仅作校准与档案初始数据参考，**不**成为系统权威依赖，见 F4）；
4. 用真实列名校准 `ads_core/ingest/fingerprints.yaml`（解决设计文档 O-1；SB/SD 种子视样本补 O-2）；
5. 从 `products.md` / 行动台账提取 6978/6979 的 campaign 清单（设计文档 O-4，供 `campaign_map` 与档案 E 组初始数据）。
