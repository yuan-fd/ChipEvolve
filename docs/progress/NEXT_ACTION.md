# 下一步

updated_at: 2026-08-06

P14/P15 已实现并完成真实验收，当前等待用户审核，不自动启动新阶段。

建议下一阶段命名为 P16“自演化研究扩展与白盒候选实证”，分两条受预算路线：

1. 扩大多个结构不同设计的真实 observation，做严格 train/validation/test、random/grid/BO 重复实验和统计置信区间，再评估更正式的离线 RL 或策略学习。
2. 在受保护 evaluator 中选择一份完整 DPLEvolve from-clean candidate，执行 baseline/candidate 的 liveness、legality、完整 RTL→GDS 和 QoR 对照；任何源码晋级继续要求人工批准。

P16 启动前应单独确定真实 EDA run 预算、候选 patch、评价指标和失败停止条件。恢复时先读：

```bash
cd ~/openroad-platform
git status --short
sed -n '1,260p' docs/evidence/P14_SELF_EVOLUTION_ACCEPTANCE.md
sed -n '1,260p' docs/evidence/P15_DPLEVOLVE_ACCEPTANCE.md
```
