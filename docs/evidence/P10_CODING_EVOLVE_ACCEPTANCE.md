# P10 Coding/Evolve Agent 与晋级闸门验收

status: completed
captured_at: 2026-08-04

## 结论

自演化闭环已形成“证据提案→隔离补丁→确定性验证→人工/策略收据”的安全版本。它没有自动修改主分支：Evolve proposal 明确 `execution_allowed=false`；Coding evaluator 只写 detached `/tmp` worktree；PromotionGate 只有收据，没有 merge/apply/push API。

## 真实仓库候选验收

在平台 P9 commit `c923f55` 上创建一个只在候选 worktree 新增说明文件的 patch：

- patch SHA-256：`163baa6425a3255240dc2724823fd91aba3805fe8ec9bbe4e04c62c4df13223d`。
- 修改路径：`docs/P10_CANDIDATE_ONLY.md`。
- `git apply --check` 和 apply 均退出 0。
- 候选内全量：81 passed，日志 SHA-256 `47afedb0...`。
- 主工作树 HEAD/status 前后相同，基线中从未出现候选文件。
- 收据：`approved_for_manual_promotion`、`applied=false`。
- 验收后 `git worktree remove --force` 精确清理 `/tmp/openroad-platform-p10-acceptance-*`；当前只剩主 worktree。

当前含 P10 的全量回归为 84 passed。

## 安全门

- patch 最多 2 MiB/50 文件，固定 base commit、SHA 和持久 evidence refs。
- 二进制、symlink、删除、mode change、rename/path traversal、`.git`、凭据和 CI/CD 路径在创建 worktree 前拒绝。
- allowed path 与 verification argv 来自受信策略，不来自 proposal；命令不用 shell，由 ProcessGuardian 管理进程组和超时。
- changed paths 必须与 patch 声明完全一致；新文件通过 intent-to-add 进入 diff 核验，但内容不 commit。
- 任一 apply/test/timeout/baseline 检查失败即 `rejected`。
- 源码路径即使通过，也默认 `awaiting_human`；批准仍只生成手工晋级收据。

## 失败证据保留

- `-01`：新文件未进入 `git diff --name-only`，触发 `changed_path_mismatch`。
- `-02`：补充 intent-to-add 后回归失败，但当时日志结果缺少输出片段。
- `-03`：补齐日志后定位到隔离环境缺 `HOME`，导致自有 Yosys wrapper 解析到错误路径。
- `-04`：在受控环境加入 HOME 后通过。

每次重试都改变假设/措施，失败目录没有覆盖。

## 验证

```text
python -m pytest -q
84 passed
```

覆盖隔离成功、语法失败、越界/CI 路径拒绝、源码人工门、基线不变、worktree 精确清理，以及 Evolve 对错版本知识的拒绝。
