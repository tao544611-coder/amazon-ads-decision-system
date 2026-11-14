# 阶段四交付文档（引擎侧支撑设施）

> 交付日期：2026-11-14 · 上游依据：`stage4-detailed-design-v1.0.md`
> **边界**：阶段四主体是运营参与的验证过程；引擎侧交付支撑设施，
> 人工核对与建议准确性评估按 `stage4-verification-checklist.md` 执行（C-1~C-9），
> 阻塞项 = 主店铺 6978/6979 报表采集。

## 已完成

| 项 | 内容 |
|---|---|
| 真实样本回归测试 | `test_stage4_regression.py`（样本缺失自动 skip）：9 份样本全链（ingest→批次判定→canonical→facts→context 结构），锚点断言 canonical 8592 / facts 8499 / immature 7.57%；漂移即失败 |
| 评估统计 | `ads evaluation stats`：verdict 分布、有效率（insufficient 不计分母）、证据不足占比、回滚建议数、SKU/动作类型分布 |
| 验收→经验草稿 | `ads experience draft <evaluation_id>`：自动拉取验收/动作/提案字段生成草稿（pending_verification，幂等），Gordon 审核后改 verified / failed（AI 错误案例通道） |
| 人工核对清单 | `stage4-verification-checklist.md` C-1~C-9（命令+通过标准+结果记录位） |

## 测试

- `test_stage4_support.py` 3 用例：有效率口径（insufficient 不计分母）/ 草稿生成幂等+检索命中 / 审核流转
- `test_stage4_regression.py` 4 用例（真实样本）
- 勘误：早期文档 canonical 锚点 8287 系汇总笔误，按各批实际输出核定为 **8592**（7781+2+73+337+306+93），已在本文件与 HANDOFF 修正

## 未验证（如实）

C-1~C-9 人工核对项全部待执行（阻塞于主店铺数据）；建议准确性评估需累计 ≥5 条真实验收。

## 下阶段入口

阶段五已并行交付（见 stage5-delivery）；全部引擎侧开发完成后，项目进入「数据到位 → 周检实操 → 经验沉淀」的运营循环。
