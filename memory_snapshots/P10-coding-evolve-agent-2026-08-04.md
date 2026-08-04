# P10 里程碑：Coding/Evolve Agent 安全闭环

captured_at: 2026-08-04
status: completed

- Evolve 只从 P9 版本匹配证据生成 `execution_allowed=false` change request。
- Coding evaluator 只在 `/tmp` detached worktree 应用受限 patch，用 ProcessGuardian 跑策略固定命令。
- 真实仓库 P9 base 候选回归 81 passed；patch SHA `163baa64...`，baseline unchanged，候选清理完成。
- Promotion receipt 为 manual promotion、`applied=false`；源码候选默认 awaiting human，无自动 merge/apply/push API。
- P10 `-01`～`-03` 失败证据保留，`-04` 通过；当前全量 84 passed。
- 证据：`docs/evidence/P10_CODING_EVOLVE_ACCEPTANCE.{md,json}`。
