# 阶段四详细设计 · 真实业务验证（引擎侧支撑设施）

> - **版本**：v1.0 · 2026-11-14
> - **上游**：PRD 第十章阶段四（历史回归 / 真实分析 / 人工核对 / 错误案例 / 修正 / 评估）
> - **边界声明**：阶段四主体是「运营参与的验证过程」。引擎侧本阶段交付支撑设施；
>   **人工核对与建议准确性评估需 Gordon 参与 + 昕信 6978/6979 报表到位**（清单见 §4）。

## 1. 真实样本全链回归测试

`tests/test_stage4_regression.py`（样本缺失自动 skip）：9 份真实样本走
ingest → 质检判定 → canonical → facts → analysis context 全链，断言与已知事实一致：
- 8 份英文报表 5 批 complete；3 份不支持源 failed（QF-01）
- canonical/facts 行数与首次冒烟一致（8592 / 8499；8287 为早期文档笔误，回归核定值见 stage1-delivery §四勘误）
- 上下文包结构完整（facts/方法论/档案/经验键齐全）
回归失败 = 引擎行为漂移，禁止带病发布（PRD 红线）。

## 2. 评估统计（建议准确性视图）

`ads evaluation stats [--from --to]`：evaluation 聚合——verdict 四值分布、
有效率 = valid / (valid+invalid+negative)（insufficient_evidence 不计入分母，
证据不足不算错误也不算成功）、insufficient 占比、rollback_suggested 数、
涉及 SKU/动作类型分布。数据来源 decision 链（阶段三）。

## 3. 验收 → 经验草稿通道

`evaluation_to_experience_draft(evaluation_id)`：自动从
evaluation(verdict/attribution) + action(动作/回滚) + proposal(原始建议) 拉字段
生成经验草稿（status=pending_verification），Gordon 审核后改
`verified` / `failed`（AI 判断错误案例即 status=failed 来源，PRD 4.11）。
CLI：`ads experience draft <evaluation_id> --by`。

## 4. Gordon 人工核对清单（阶段四验收必做项，docs/stage4-verification-checklist.md）

清单化管理：每项核对有操作命令、期望、通过标准、结果记录位。
