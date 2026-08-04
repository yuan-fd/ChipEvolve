# P5 里程碑：AgenticPD 提案与真实候选对比

captured_at: 2026-08-04
status: completed

- `agenticpd@1.0.0` 固定 `4322a25c...`，只作为黑箱 proposal producer；Runtime 仍是 child run 和终态唯一权威。
- 新增严格 `ExperimentPlan`/`ExperimentCandidate`；完整上游参数留证，首版只激活可核验的 `CORE_UTILIZATION`。
- 同一 RTL/toolchain/PDK/约束真实执行 38% 与 35% Nangate45 GDS，两个 config 均证明参数已消费，DRC 均为 0。
- 候选功耗略低，但 WNS/线长略差；阶段不挑最佳值，也不声称 mock QoR 真实。
- Adapter 若真实模式无 `DEEPSEEK_API_KEY` 会 fail closed；真实 LLM 仍是外部阻塞。
- ORFS 会登记多个 `report` kind；QoR 读取必须明确选择 `analysis/report.json`，不能取首个 `plan.json`。
- 证据：`docs/evidence/P5_AGENTICPD_ACCEPTANCE.{md,json}`；成功原始目录 `runs/p5-acceptance-20260804-02/`。
