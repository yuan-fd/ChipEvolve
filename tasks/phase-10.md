# P10 任务：Coding/Evolve Agent 与晋级闸门

status: completed
phase: P10
started_at: 2026-08-04
completed_at: 2026-08-04
base_commit: c923f55

## 白名单

- `packages/analysis/`：证据驱动、data-only Evolve proposal。
- `packages/execution/`：隔离 worktree patch evaluator 与 PromotionGate。
- `scripts/run_p10_acceptance.py`、`tests/test_coding_evolve_agent.py`。
- P10 任务、最终证据、进度、长期记忆和项目状态。
- 候选 worktree 仅在 `/tmp/openroad-platform-p10-*`；验收摘要在 ignored `runs/p10-*`。

## 禁止范围

- Agent 不直接修改、commit、merge、push 或部署主工作树。
- patch 不能触及 `.git`、凭据、CI/CD、二进制、symlink 或白名单外文件。
- verification command 由策略预先固定，不来自模型/patch；不用 shell。
- P9 检索文本不是代码或命令；源码改动默认要求人工批准。

## 验收门

- Evolve proposal 必须携带 evidence ref/SHA/fingerprint 且 `execution_allowed=false`。
- Coding evaluator 在 detached worktree 检查 patch SHA/path、应用补丁并用 ProcessGuardian 跑固定回归。
- 候选失败、超时、越界均留结构化结果；主工作树 HEAD/status/content 前后完全一致。
- 源码候选通过也只能得到 `awaiting_human`，没有自动应用 API。
- 在 P9 commit 上真实跑一个 docs-only 隔离候选和全量测试，然后清理 worktree。

## 预算与停止条件

- patch ≤2 MiB、文件≤50、命令≤10、单命令≤600 秒；验收候选一次。
- 任一检查失败即拒绝晋级，不尝试自动修补候选。
