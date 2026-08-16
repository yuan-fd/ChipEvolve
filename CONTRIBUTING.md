# Contributing

OpenROAD Platform welcomes focused research and engineering contributions, but
its evidence boundary must remain stricter than its proposal boundary. Code,
models, users, and external tools may propose a change; only deterministic
validation and recorded Runtime evidence may establish a result.

## Before opening a change

1. Create a focused branch from the reviewed baseline.
2. State whether each important claim is an observed fact, an external
   documented claim, or a hypothesis.
3. Identify affected contracts, toolchains, databases, and artifact kinds.
4. Keep unrelated working-tree changes intact and outside the commit.
5. Do not add credentials, PDK data, local tools, live databases, or generated
   run workspaces to Git.

## Change design

- Prefer backward-compatible changes. Version a contract when compatibility
  cannot be preserved.
- Keep Web/API submission separate from worker execution.
- Allowlist user-controlled inputs and environment variables. Do not expose an
  arbitrary shell or unrestricted file path through a plugin.
- Preserve immutable attempt workspaces and append-only evidence. A retry is a
  new attempt; a failed run is not rewritten as success.
- Make optional integrations fail closed and describe their verified boundary.
- Keep comments and docstrings short and focused on non-obvious invariants.

Plugin contributions must follow [`docs/PLUGINS.md`](docs/PLUGINS.md), including
source/environment locks and a third-party license audit.

## Verification

Run the smallest relevant test set while developing. Before review, run:

```bash
python3 -m pytest -q
python3 scripts/check_tracked_secrets.py
git diff --check
git status --short
```

If the sandbox prevents a loopback fixture from binding, rerun the same tests
in the approved local host environment and report both outputs. Do not weaken a
test or security control to accommodate the sandbox.

For a real EDA flow, include:

- exact source/toolchain commits and binary versions;
- command line and bounded configuration;
- Runtime DB, run ID, attempt ID, and terminal status;
- metric names/values and artifact count;
- paths and SHA-256 hashes for key evidence;
- failures and abandoned attempts as well as the successful attempt.

## Commit and review checklist

- [ ] Scope is focused and unrelated changes are excluded.
- [ ] Contract/API behavior and migration impact are documented.
- [ ] Failure, timeout, cancellation, and missing-artifact paths are tested.
- [ ] No credential, PDK, generated cache, or live evidence is tracked.
- [ ] Documentation distinguishes verified facts from future capability.
- [ ] Focused and full regression output is attached to the review.
- [ ] No shared worker, deployment, remote branch, or toolchain was mutated as
      an unreviewed side effect.

Use imperative commit subjects such as `feat: add ...`, `fix: reject ...`, or
`docs: explain ...`. Keep generated acceptance evidence in its designated
evidence directory only when the project explicitly requires it.

## Community expectations / 协作约定

Be precise, respectful, and reproducible. Review the evidence rather than the
author, disclose uncertainty, and preserve failed experiments that explain a
decision. Security or licensing concerns should be raised privately with the
maintainers until a disclosure channel is documented.

贡献应保持范围清晰、证据可复现、结论不过度。请勿把凭据、PDK、本地工具链、实时
数据库或临时运行目录提交到 Git；不要在未获授权时 push、部署、重启共享 worker，
也不要覆盖其他人的工作树改动。项目当前缺少顶层 LICENSE，在维护者明确许可证前，
外部贡献和再分发必须先确认许可边界。
