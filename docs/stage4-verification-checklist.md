# 阶段四人工核对清单（Gordon 参与项）

> 引擎侧支撑设施已交付（回归测试 / 评估统计 / 经验草稿，见 stage4-delivery）。
> 以下为 PRD 阶段四要求的**人工验证项**——每项给出操作命令与通过标准。
> 前置阻塞：采集含 6978/6979 活动的昕信广告报表（当前样本为约德账号）。

| # | 核对项 | 操作 | 通过标准 | 结果记录 |
|---|---|---|---|---|
| C-1 | 识别与导入核对 | 采一份昕信 Campaign/Targeting 报表 → `ads ingest <文件> --json` | 类型识别正确（人工对照后台报表类型）；QF 无 error | 本表下方追加 |
| C-2 | 指标复算核对 | 导入后 `ads facts build --json`，抽查 2 个活动某日 spend/clicks 与亚马逊后台一致 | 差异 ≤1%（T-2 报表延迟内） | 同上 |
| C-3 | 成熟度标注核对 | `ads facts maturity` 对比后台归因窗说明 | 未成熟行日期与 SP7/SB14 窗口一致 | 同上 |
| C-4 | 上下文包可读性 | `ads analysis context --sku 6978 --from <周一起> --to <周日> --dump` 人工读包 | 档案/事件/方法论/成熟度标注齐全可用；无敏感信息泄漏 | 同上 |
| C-5 | AI 建议质量（T5 核心） | AI 读包产建议 → `ads analysis validate` → 人工逐条审 | 建议有据可查；reasoning 提及证据与规则；被拒建议的违规码合理 | 同上 |
| C-6 | 审批流实操 | `ads proposal approve`（含一次 modify 改幅度） | 终稿保存正确；未批建议无法建动作 | 同上 |
| C-7 | 观察与验收实操 | 周检时 `observation complete` + `evaluation record` | 干扰因素（秒杀/调价/跟卖）被列出；证据不足时判 insufficient 而非硬判 | 同上 |
| C-8 | AI 错误案例沉淀 | 发现 AI 判断错误 → `experience draft` 或 `experience add`（status=failed） | 错误案例入库保留（不删） | 同上 |
| C-9 | 建议准确性评估 | 累计 ≥5 条验收后 `ads evaluation stats` | 有效率/证据不足占比可供经营判断；异常高 insufficient → 检查数据源 | 同上 |

## 结果记录（追加制）

- （暂无——待昕信数据到位后逐项回填）
