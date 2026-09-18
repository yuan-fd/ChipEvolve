# Frequently asked questions

[中文版本](FAQ.zh-CN.md)

## Why use this if Codex can already run scripts?

Codex can decide which script to write. A useful experiment also needs input
snapshots, a working directory, process cleanup, timeouts, logs and traceable
outputs. Without a common execution layer, each agent has to rebuild that
bookkeeping. Here an agent still chooses the experiment; the foundation
records and manages its execution.

## Is this an OpenROAD automation workbench?

No. The repository name and Python package names retain their history, but the
kernel does not contain OpenROAD or ORFS flow logic. The real ORFS integration
is an external Toolkit. Innovus and PrimeTime are possible integrations, not
tools we claim to have validated. The first test on a new server should be the
[local example](../examples/quickstart/README.md), which needs no EDA tool.

## Toolkit or Plugin: which should I implement?

Implement a Toolkit with an Adapter, package it using the existing plugin
manifest. Toolkit describes the capabilities; Adapter starts the tool and
collects results; Plugin is the discoverable package. These are roles, not
three competing protocols. Keep `plugin_id`; do not invent a second API.

## Can an agent submit its own script or source patch?

Yes. Stage it using `staged_inputs`, and select a Toolkit capability that knows
how to execute the script or apply/build the patch. The kernel does not parse
TCL, Python or patch semantics. See the
[script and patch example](../examples/research-toolkit/README.md).
Only run code you trust: a workspace is not an OS security sandbox.

## Can I move this to the team's other server?

Yes, but a successful run on this machine does not prove that another machine
has the same tools. Clone the platform and the required external Toolkit,
install platform packages, choose a fresh writable state directory, configure
absolute tool/interpreter/library paths and run the local example first.
Then run a small real design through the Plan API. Record architecture, tool
versions, Toolkit revision, inputs, logs and output hashes on that server.

Do not copy a live SQLite directory or reuse this machine's absolute paths.
For existing experiments, stop services before taking a consistent backup of
the full state directory. Relocating old state and absolute workspace paths
is not currently a tested migration workflow.

## How should teammates connect?

For now, use trusted accounts on the server or SSH port forwarding. The local
launcher listens on `127.0.0.1:8700` and `127.0.0.1:8840`. Loopback means
local to the machine, not private to one Unix user. The no-auth instance
is a shared trust domain. It is not a multi-user ownership boundary.
Coordinate use of one instance; independently managed instances need distinct
ports and state directories.

For example, from your laptop:

```bash
ssh -L 18700:127.0.0.1:8700 user@team-server
```

Use `http://127.0.0.1:18700` as the gateway URL on the laptop. Input source
paths still refer to the server's files, not the laptop's.

## The plugin is missing from the catalogue. What should I check?

The scan root contains child directories, each containing a `*.plugin.json`.
Point `OPENROAD_PLATFORM_PLUGINS_ROOT` at the parent directory, not the
manifest itself. Validate the package:

```bash
.venv/bin/openroad-platform-plugin-validate /absolute/path/to/my-toolkit
```

Validation does not run the tool or grant admission.

## The plugin is visible but execution is refused. Why?

Check the plugin's architecture, requested version and platform-owned admission
record. An admitted package requires license review and a reviewed commit.
If provenance declares a commit, it must match the admission. Do not copy the
example's all-zero fixture revision into a real Toolkit approval.

## The tool works in my shell but not in an attempt. Why?

The adapter inherits only an allowlist of host environment variables, plus the
manifest's environment. Your interactive shell's `PATH`, `LD_LIBRARY_PATH`,
virtual environment and license setup may not be available. Use an absolute
adapter executable, declare the required non-secret environment and check the
tool's dynamic libraries. Keep real credentials out of Git and recorded inputs.
Manage sensitive environment configuration locally under the team's policy.

## The process exited with zero. Why did the run fail?

The adapter must write a valid result whose exit code matches the process.
Required declared artifacts must exist, be nonempty and be inside the
workspace. Metrics must reference registered evidence. A missing result,
missing output or invalid metric source can fail the run even if the tool
returned zero. Inspect the run failure, logs and artifacts together.

## What does `execution_valid: true` mean?

All plan steps reached success. It does not prove that a chip meets timing,
DRC, signoff or the experiment's scientific goal. A Toolkit/evaluator defines
those criteria, and the Agent decides whether results are comparable.

## Why is a repeated plan rejected or not a new experiment?

Plan identifiers are durable. Use a new `plan_id` for a new experiment; do not
expect resubmission to restart a completed plan. Child submissions use stable
plan-scoped idempotency keys. Changing a task while reusing a kernel
idempotency key is a conflict, not a new run.

## Why does the task remain queued?

Check that the worker is alive and uses the same state, plugin and admission
directories as the gateway. Check requested resources against available
capacity and reservations. A healthy HTTP endpoint alone does not mean a
worker is consuming the queue.

## Does the platform enforce hard CPU/memory isolation?

No. It reserves capacity, samples process-tree usage and terminates an attempt
when a sampled limit breach is detected. This is not a cgroup hard limit or a
container sandbox. Configure conservative capacity on a shared machine.

## Is the repository completely lint-clean?

No. The latest recorded full test result is 438 passed and one intentionally
skipped branch; 39 architecture tests passed. Targeted Ruff and source mypy
checks passed. Historical repository-wide formatting and static-check debt is
documented in the [audit](internal/EXECUTION_AUDIT_REPORT.md). A green test
suite is not a claim that every quality tool is clean.

## What should I include in a bug report?

Include platform and Toolkit commits, OS/architecture, the failing command,
redacted task/plan, plan ID, run ID, failure category and relevant log excerpt.
For toolchain problems include executable paths, versions and missing-library
messages. Do not attach proprietary designs, license credentials or large
tool databases to a public issue.
