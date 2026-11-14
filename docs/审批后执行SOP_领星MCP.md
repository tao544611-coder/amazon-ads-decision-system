# 审批后执行 SOP：AI 会话经领星 MCP 执行已批修改

> 架构定案（Gordon 2026-11-14）：审批门批准后，执行回到 agent——AI 会话读取已批动作，通过领星 MCP 工具面执行修改，回执写回本地决策链。
> 适用：所有 `action.status = pending` 且 `execute_mode = manual` 的动作。
> 版本：v1.0（2026-11-14）

## 一、执行者与触发

- **执行者**：AI 会话（ZCode 等，拥有领星 MCP 工具面）
- **触发**：审批门批准（approve / modify_approve）并生成待执行动作后；或 Gordon 直接指示执行某动作
- **不执行**：Web 审批门只负责到「批准/生成动作」为止，页面永不直连领星

## 二、动作类型 → 领星工具映射（随工具到位更新）

| 动作类型 | 领星工具 | 状态 |
|---|---|---|
| 加 SP 否定关键词 / 否定 ASIN | `advertising_add_negative_keywords` / `advertising_add_negative_targets` | ✅ 可执行 |
| 加 SP 关键词 | `advertising_sp_add_keywords` | ✅ 可执行 |
| 归档 SP 否定投放 | `advertising_sp_archive_negatives` | ✅ 可执行 |
| 创建 SP/SB/SD 活动、广告组、广告 | `advertising_create_*` | ✅ 可执行 |
| **关键词/投放出价调整** | （无，需求单 P1） | ⏳ 等领星补工具 → 人工后台改 |
| **活动预算修改、状态启停** | （无，需求单 P1） | ⏳ 等领星补工具 → 人工后台改 |
| 观察（observe） | 无领星操作 | 人工按验收标准核对 |

映射表更新规则：领星每发布新 write 工具，先 `search` 读 Schema，核对字段与本地动作语义一致后再入表。

## 三、执行循环（七步，逐步留痕）

1. **取动作**：`ads action list --status pending --json` → 取 `id`、`proposal_id`；用 `ads proposal show` 读终稿（审批修改后的 payload 为准，不是原始建议稿）。
2. **对象核对（硬闸）**：动作 target 里的活动/广告组/关键词标识，与领星侧实际对象核对（活动名→campaignId 经 `ad_campaign_report` 等读工具反查；店铺 `profileId`/`sid` 经 `ad_auth_shops` 确认）。**对不上立即停**，回报 Gordon，不猜测不硬凑。
3. **匹配工具**：按第二节映射表选 `toolId`；无对应工具 → 不执行，标注「人工处理」并如实回报。
4. **执行前快照**：用对应 read 工具记录执行前状态（如当前出价/预算/状态），写入回执 `before`。
5. **执行**：`search` 读 Schema → 构造参数 → `action` 调用（单次建议 1–10 条调整，逐条或批量按 Schema 支持）。**失败不盲目重试**：同一动作最多重试 1 次，仍失败标 `failed`。
6. **确认终态**：`approval_status` 轮询（若返回审批凭证）至成功/失败/拒绝。
7. **回执落库**：`ads action receipt <action_id> --result success|failed|unknown --operator ai-agent --file 回执.json`，回执 JSON 含 `before` / `after` / `raw`（领星原始返回）+ 领星操作日志编号。之后按提案的观察窗走周检验收。

## 四、安全红线

- 只执行 `status=pending` 的动作；已执行/已回执的动作不重放（引擎幂等键兜底）。
- 执行前必须完成对象核对（第二步），领星侧对象与提案 target 不一致 = 停。
- 幅度护栏由引擎在校验关保证（±15%、£0.10~2.00）；agent 不二次改数字，改数字走审批门「修改批准」。
- 凭证（X-Mcp-Key 等）不进命令输出、不进回执、不进日志。
- 全程输出执行摘要（做了什么、领星返回什么、回执 ID），不堆原始 JSON。

## 五、首次实测记录（2026-11-14，全流程 ✅）

Gordon 直令实测：TESTNGT-ACZPortable Phrase 活动加精确否定词 microwave。七步全过，全程约 3 分钟。

| 步骤 | 实测结果 |
|---|---|
| 对象核对 | `ad_auth_shops` 定位店铺（主店铺MAIN-UK sid=11111）→ `ad_sp_campaigns` 定位活动 campaign_id=444444444444；同店 6 个映射活动 ID 与生产库 CampaignMap 逐一吻合（双重验证） |
| 执行 | `advertising_add_negative_keywords` 一次成功：targetId=660000000000，EXACT/microwave/negative/ENABLED，活动层级，GB 市场 |
| 审批闸 | **未触发**（该工具 approvalDisplay 为空 → 即时生效）；带审批闸的工具届时用 approval_status 轮询 |
| 事后验证 | `ad_operation_log_standard`（operate_type=negativeKeywords）确认 create 落库，object_id 与 targetId 一致，function_name=OpenApi，user_name=领星账号实名 |

### 实测沉淀的四条经验（已并入执行循环）

1. **无 SP 否定关键词列表工具**（MCP 只有否定投放 ASIN 列表）→ 否定词的执行前快照/事后验证统一走 `ad_operation_log_standard`。
2. **操作日志 start_date 不能是当天**（报「必须是过去的时间」）→ 查当天操作要 start_date=前一日、end_date=当天。
3. **操作日志时间为站点时区**（UTC，GB=本地-8h）→ 对账时注意换算。
4. **领星两类店铺 ID 同源**：`get_my_sids` 的 id 与 `ad_auth_shops` 的 sid 数值一致（本账号实测），广告工具用 sid，ERP 工具用 get_my_sids。

> 注：本次为 Gordon 人工直令，不经提案链（人工直令优先于决策流）；AI 自发建议仍必须走「建议→三关→审批→动作→执行」全链。
