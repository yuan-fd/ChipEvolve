# P13 stage-aware Campaign 验收

status: accepted
date: 2026-08-05

ORFS adapter 现在输出 allowlisted stage start/finish 协议；Runtime 将其转换成 `tool.stage.started`/`tool.stage.finished` 追加事件，并校验 run、外层 StageRun 和 Attempt 归属。第三方 stdout 不能伪造其他 Runtime 状态转换。

`StageAwareCampaignManager` 支持受限参数笛卡尔网格、并发上限、每阶段 wall-clock 预算、运行中取消/剪枝、终态失败分类、LimitedReAct 修复子 Task、新 Run/Attempt 留证、指标 Top-K 和追加决策日志。首版 doomed-run 是可解释规则，不宣称机器学习预测器。

真实验收 Campaign `campaign-51977997ac5d466e977649e0b473493d` 以最大并发 2 运行 10%/20% 两个真实 Nangate45 ORFS synth 候选，均成功；每个 run 均登记 synth start/finish。单测另行覆盖慢阶段剪枝、Top-K 和拥塞修复子 run。

原始证据：`.tools/p13-acceptance/runtime-20260805/`。
