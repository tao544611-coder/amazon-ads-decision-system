# D11 二期交付：领星 MCP JSON 适配器（2026-11-15）

分支 `feature/lingxing-json-adapter`。背景：2026-11-15 数据盘点确认两个自动化项目（daily-monitor / auto-ads-check）的原始数据记录均为领星 MCP 窗口聚合 JSON 或非广告域数据，无一满足入库条件；Gordon 选定路径 B（为领星 MCP JSON 写正式适配器）。

## 交付范围

**适配器 `lingxing_mcp_json`**（`ads_core/adapters/lingxing_mcp_json.py`）：

- 输入：领星开放平台 MCP 只读接口的原始 JSON 响应（自动化按日拉取后落盘的存档文件），支持三种形态——MCP 响应外壳（`data.data` / `data.list` / `data.records`）、存档形态（顶层 `rows`）、顶层行数组。
- 首个覆盖报表：`ad_sp_campaign_report`（SP 广告活动报表，**日粒度**，行含 `report_date`）。
- 映射：`report_date`→date、`campaign_id`→业务键、`cost`→spend、`impressions/clicks/orders/units`→整型、`sales`→浮型；`campaign_name` 允许为空（该接口不返回名称，归属靠 CampaignMap 按 campaign_id 登记）；marketplace/currency 取导入参数（响应无此字段）。
- 识别：新增指纹变体 `lingxing_mcp_daily_campaign`（maps_to=campaign，区分列=profile id / same_* 系列直成交列）。
- **窗口聚合 JSON 的明确拒绝**（PRD 红线：不虚构日期归属）：行缺 `report_date`/`date` → 适配器行级 AdapterError（指明改为按日拉取）；自动识别路径下（无日期键 → 日期组不命中 → unknown → QF-01 error → 批次 failed + raw rejected）。两条路径都不静默入库。
- 基础设施增补：`read_table` 支持 `.json`；`importer._data_dates` 与 QF-02 业务键日期列候选补 `report date`；MCP `ingest_file` 增加可选 `source_system` 参数（向后兼容）。

## 边界（如实说明）

- 窗口聚合类 JSON（`ad_campaign_product_report`、`ad_campaign_keyword_report` 汇总、`5SKU明细_flat.csv` 同构数据）**依旧无法入库**——它们没有日期字段，这不是适配器能解决的：自动化侧必须改为按日拉取（`ad_sp_campaign_report` 单日查询已在缺陷修复验证中跑通）。适配器把它们明确挡在门外并给出改进指引。
- `product_performance.json`（周聚合销售表现）与 daily-monitor 的 BSR/价格/排名数据（产品表现域/市场域）不属于广告事实层，维持周检对照旁路，不入库。

## 验证

- 新增测试 `tests/test_d11_json_adapter.py`：10 项（reader 4 + 识别 2 + e2e 合成 3 + 真实存档 1），全绿。
- 真实存档 e2e：`data/raw/6978_6979_validation_20261114/` 的 36 行日级 JSON 直接入库 → canonical → facts，与缺陷修复验证的 CSV 路径锚点完全对齐（花费 £175.88 / 点击 434 / 展示 44,608 / 订单 39 / 销售 £684.00；成熟度 28 成熟 / 8 未成熟）。存档行已依原响应补录 `profile_id`（D11 二期识别区分列需要，值未变）。
- 全量回归：135 用例（125 + 10）。

## 自动化接入方式

```bash
# 自动化按日拉取后落盘 JSON，直接入库：
ads ingest <响应.json> --source-system lingxing_mcp_json --ad-product-type sp
# MCP 等价：
# ingest_file(file_path=..., source_system="lingxing_mcp_json")
# 之后：canonical build → facts build，活动归属按 campaign_id 登记 CampaignMap
```

## 待办（后续）

- 自动化（auto-ads-check / daily-monitor）改造为按日拉取并落盘——待 Gordon 测试后决策
- 其他日粒度领星 JSON 报表（关键词/投放/广告位）接入：同款适配器模式，等自动化拉取到位
- 3SKU 明细 CSV（214 行日级）补指纹入库（原路径 A ①）仍可选做
