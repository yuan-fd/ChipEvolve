# Contributing

[中文指南](CONTRIBUTING.zh-CN.md)

This project is an execution foundation, so a contribution should make the
execution contract clearer or more reliable. Before changing code, identify
which layer owns the behavior. A new EDA command normally belongs in an
external Toolkit repository, not in `core/runtime`.

## 1. Set up a clean checkout

```bash
git clone <repository-url> openroad-platform-v2
cd openroad-platform-v2
tools/install-local.sh
```

The installer creates a local virtual environment and installs the repository
packages without fetching runtime dependencies. If the environment already
exists, rerun it after changing package metadata.

Run the fast boundary example first:

```bash
python3 -m pytest -q \
  apps/plan_executor/test_against_kernel.py \
  -k test_agent_generated_code_is_executed_and_measured
```

This starts a real local gateway, worker and plan executor. It tests an
Agent-authored script and patch/build/benchmark tasks through the normal
execution path; it does not call the adapter as a unit-test shortcut.

## 2. Decide where the change belongs

Use the [architecture overview](docs/ARCHITECTURE_OVERVIEW.md) before editing:

- public task/result/input fields: `contracts/`;
- workspace, process, resource, retry or artifact lifecycle: `core/runtime/`;
- Toolkit discovery, manifest or admission: `core/registry/`;
- HTTP composition and routes: `gateway/` or the owning app;
- ordered plans and artifact handoff: `apps/plan_executor/`;
- tool commands, report parsers and capability semantics: an external Toolkit;
- architectural rules: `guardrails/` plus a deliberately failing fixture.

The kernel must not learn OpenROAD, Innovus, PrimeTime, ORFS stage order or QoR
policy. The plan executor must not invent a flow that the Agent did not submit.

## 3. Add or change a Toolkit

Create the Toolkit as its own directory or repository. Follow this sequence:

1. copy `examples/research-toolkit/` as a starting point;
2. write the manifest and `provenance.json`;
3. implement `--request`/`--result` in the adapter;
4. declare every output in `artifact_rules`;
5. return metrics with an artifact source path;
6. add platform-owned admission evidence for the reviewed revision;
7. exercise it through a real Task or Plan, not only a direct process call;
8. document the tool prerequisites and one known-good command.

The [Toolkit protocol](docs/PLUGIN_PROTOCOL.human.md) is the source of truth
for exact fields and refusal behavior. Capability names and their parameters are
free-form Toolkit data; do not ask the kernel to normalize them.

## 4. Change behavior test-first

For a bug, first add a regression test that fails against the old behavior.
Then make the smallest implementation change, run the focused test, and run
the surrounding package tests. Keep tests focused on observable state: run
status, evidence, failure classification and API response rather than private
method calls.

For a new contract field, update the contract tests and at least one end-to-end
boundary test. For a lifecycle change, cover both success and the relevant
failure or cancellation path.

## 5. Run the checks

Before committing code, run the checks that cover the changed layer:

```bash
python3 -m pytest -q
python3 -m pytest -q guardrails
ruff check <changed-python-files>
mypy --no-error-summary --show-error-codes --cache-dir /tmp/platform-mypy \
  contracts/src core/*/src gateway/src apps/*/src
git diff --check
```

The repository also has deliberate negative fixtures under
`guardrails/negative/`. Do not “fix” those fixtures to make the suite green.
Do not raise the code-size ceiling or disable a rule to hide a violation.

For a real ORFS change, use the external ORFS acceptance test and record the
tool versions, paths, commit revisions and generated artifact evidence. A
process exit code alone is not enough to call a design experiment valid.

## 6. Write documentation that explains the decision

Update the README when the user-visible purpose or quick start changes. Update
the architecture overview when ownership or data flow changes. Update the
protocol when a Toolkit-facing rule changes. Record a durable architecture
decision in `docs/decisions/` when alternatives and trade-offs matter.

Prefer plain language and concrete examples. Say what a developer should do,
what the platform does for them, and what remains their responsibility.

## 7. Commit and submit

Keep commits focused and descriptive:

```bash
git status --short
git diff --check
git add <files>
git commit -m "fix: preserve artifact evidence on retry"
```

Do not commit `.env`, credentials, generated workspaces, EDA databases or
private tool installations. A Toolkit's source and platform admission evidence
should make it possible for another developer to reproduce the test without
sharing secrets.

Pull requests should include:

- what changed and why;
- the layer and public contract affected;
- tests and commands run;
- known limitations or follow-up work;
- whether a real Toolkit or EDA tool was required.

## Review standard

A contribution is ready when another developer can answer these questions from
the diff and its tests:

1. What user-visible behavior changed?
2. Which component owns that behavior?
3. What happens on timeout, cancellation and tool failure?
4. Can the result be traced to a workspace, artifact and metric?
5. Did the change add unnecessary EDA policy or defensive complexity?

The current protocol is `v1alpha1`; public contract changes require an explicit
compatibility note rather than a silent migration.
