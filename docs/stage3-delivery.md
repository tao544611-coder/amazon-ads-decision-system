# 阶段三交付文档（按 PRD 十一章模板）

> 交付日期：2026-11-14 · 上游依据：`stage3-detailed-design-v1.0.md` · 前置：阶段一/二（52 用例全绿）

## 一、已完成功能

| 模块 | 内容 | PRD 对应 |
|---|---|---|
| 数据模型 | 迁移 `003_stage3_decision_loop`：proposal / approval / action / execution_receipt / observation / evaluation / experience 七表 + `experience_fts` FTS5 虚表 | 4.8–4.11 |
| 提案管理 | `submit_proposals`：校验通过建议（passed）迁入提案表（继承方法论+档案版本引用）；未过校验建议不可入提案 | 4.8 / D4 |
| 人工审批 | approve / **modify_approve（运营终稿必须保存）** / reject / defer；deferred 可重新提请；自批留痕（self_approval，O3-1） | 4.8 / T6 |
| 动作管理 | `create_action` 守卫 G1–G3：未审批不可建（审批与执行分离）、`idempotency_key=proposal:approval` 唯一（Agent 重跑安全）；simulated/manual 两种模式（无后台写入） | 4.9 / T6/T8 |
| 执行回执 | `record_receipt`：success/failed/unknown；**unknown → verify_status=state_unknown（先查后台，禁止盲目重执行）**；回执多条=重试历史完整保留 | 4.9 / T8 |
| 回滚 | `rollback_action`：executed→rolled_back，原因/操作人/时间留痕，历史不删（O3-3 永不自动执行） | 4.10 / T8 |
| 观察窗口 | `create_observation`：F5 窗口 = max(验证窗 7 天, 归因满窗 SP7/SB14 从严)；基准快照（执行前 7 天成熟行）+ **同期业务事件/其他动作自动拉取（T7 干扰识别）** | 4.10 / F5 |
| 效果验收 | `record_evaluation`：四裁决 valid/invalid/negative/**insufficient_evidence**；归因四层结构（what_changed/possibly_related/strongly_supported/other_factors）；**存在干扰且未排除时禁止强行判定**（G6）；invalid/negative 且预设回滚条件 → rollback_suggested（只建议） | 4.10 / T7 |
| 经验库 | `add_experience` / `search_experience`：FTS5 中文二元切分（D7）、OR 召回 + rank 排序；失败经验保留无删除路径；上下文包接入 `related_experience`（标注「辅助依据，不覆盖方法论」） | 4.11 / D3 |
| CLI | proposal submit/list/approve · action create/list/receipt/rollback · observation create/complete · evaluation record · experience add/search/list（全 `--json`） | Q1 |
| 审计 | 决策链全部状态变更落 `audit_log` | 7.3 |

## 二、代码与目录

```
ads_core/ads_core/decision/lifecycle.py（+ __init__）
ads_core/ads_core/experience/library.py（+ __init__）
ads_core/ads_core/analysis/context.py（增补 related_experience）
ads_core/alembic/versions/003_stage3_decision_loop.py
tests/test_stage3_decision_loop.py（12 用例）
```

## 三、测试用例与运行结果

**命令**：`.venv/Scripts/python.exe -m pytest tests/ -q`
**结果：64 passed, 0 failed**（阶段一 38 + 阶段二 14 + 阶段三 12，2026-11-14 本机）

| 组 | 数 | 断言 |
|---|---|---|
| T6 权限 | 4 | 未审批建动作拒 / rejected 永不进 / modify 终稿保存且 action 引用真实 approval / 自批留痕 |
| T8 幂等回滚 | 3 | 同提案同审批重复建返回同一 action / 回执 unknown→state_unknown（补查后可修正，历史 2 条保留）/ 回滚后历史完整且不再收回执 |
| F5 窗口+全链 | 1 | 窗口=[执行日, +14 天]；baseline/post 快照；valid 验收落库 |
| T7 归因 | 2 | 同期事件自动拉取；未排除干扰的 valid 拒、insufficient_evidence 合法；invalid+回滚条件→rollback_suggested |
| 经验库 | 2 | 中文二元检索命中 / 失败经验保留 + 上下文含 related_experience（含使用限制标注） |

## 四、真实库端到端冒烟（2026-11-14）

1. 校验失败建议（阶段二演示 run 的 EV-01 拒绝项）→ `proposal submit` 正确不入提案（skipped_verdict=1, created=[]）
2. 沉淀真实经验 `EXP-2026-11-14-001`（台账 2026-11-09 W45 归因结论：三混杂未排除不归因）→ 中文检索「归因 混杂 评论聚合」命中

## 五、已知问题

1. 观察窗自动完结（窗口到期自动拉数）未实现——`observation complete` 手动触发（O3-2，自动调度属阶段五）
2. 动作执行仅 simulated/manual 记录；后台写入的独立授权/回读/异常处理按 PRD 4.9 属后续开放能力
3. rollback_suggested 只提示不执行（O3-3 设计如此：模拟回滚+人工处理）

## 六、未实现（阶段三范围外）

- 阶段四：真实业务回归验证（历史数据回归、AI 错误案例记录、建议准确性评估）
- 阶段五：MCP Server 封装（ads_* 工具直接包装 service 层）、自动调度

## 七、项目当前总览

PRD 5 阶段已完成 3 个：数据管道与事实指标（一）→ 分析上下文与建议校验（二）→ 审批与观察闭环（三）。T1–T8 中 T1/T2/T3/T4/T5/T6/T7/T8 引擎侧测试全部就绪（64 用例）。剩余：阶段四真实业务验证、阶段五 MCP 开放。
