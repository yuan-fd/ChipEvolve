# Agent operations

This is the runbook for an Agent or Codex operating the platform on a newly
provisioned EDA server.

## Operating rule

Decide the experiment, choose or compose Toolkit capabilities, express the
experiment as a plan, submit it, and read the recorded evidence. An Agent may
provide parameters, scripts, build inputs, or patches. Do not invoke EDA
binaries outside the execution foundation, invent workspaces, copy outputs by
hand, or retry by overwriting a previous run. Every step in a plan creates a
normal immutable task and every attempt keeps its own evidence.

## Bootstrap a clean server

1. Clone this repository and the required external plugin repository.
2. Install the platform without fetching runtime dependencies:

   ```bash
   cd /srv/openroad-platform-v2
   tools/install-local.sh
   ```

3. Confirm the EDA paths are absolute and executable. For ORFS, identify:

   ```text
   /srv/OpenROAD-flow-scripts
   /srv/eda/bin/openroad
   /srv/eda/bin/yosys
   /srv/eda/bin/klayout
   ```

4. Start the local execution base:

   ```bash
   export OPENROAD_PLATFORM_PLUGINS_ROOT=/srv/agenticeda-orfs
   export OPENROAD_PLATFORM_STATE_ROOT=/srv/openroad-platform-state
   tools/start-platform.sh
   ```

5. Check both services:

   ```bash
   curl -fsS http://127.0.0.1:8700/health
   curl -fsS http://127.0.0.1:8840/health
   ```

`OPENROAD_PLATFORM_PLUGINS_ROOT` must point at the directory containing the
plugin subdirectories and their `*.plugin.json` files. Admission remains a
platform decision: `admissions/<plugin_id>.json` must match the plugin's
declared provenance.

## Submit an EDA plan

Copy `examples/orfs-gcd-plan.json`, replace every `/srv/...` path with a real
path on this server, and submit it:

```bash
.venv/bin/python tools/verify-plan.py /tmp/orfs-gcd-plan.json --timeout 7200
```

The plan service is also directly accessible:

```text
POST http://127.0.0.1:8700/app/plan_executor/plans
GET  http://127.0.0.1:8700/app/plan_executor/plans/{plan_id}
POST http://127.0.0.1:8700/app/plan_executor/plans/{plan_id}/cancel
```

Kernel evidence is addressed by each step's `run_id`:

```text
GET  /kernel/runs/{run_id}
GET  /kernel/runs/{run_id}/logs?offset=0&max_bytes=8192
GET  /kernel/runs/{run_id}/timeline
GET  /kernel/runs/{run_id}/artifacts
GET  /kernel/runs/{run_id}/metrics?complete_only=1
GET  /kernel/runs/{run_id}/resources
POST /kernel/runs/{run_id}/cancel
POST /kernel/runs/{run_id}/retry
```

Query the exact toolchain evidence for an ORFS run with:

```text
GET /kernel/runs/{run_id}/artifacts?category=environment&format=toolchain-snapshot
```

Exactly one result with a platform-computed SHA-256 is expected. Its content
records executable paths, executable hashes and versions, ORFS revision and
dirty state, configuration/RTL hashes and the toolchain fingerprint.

## Interpret the result

- `execution_valid: true`: every plan step succeeded. The experiment is valid
  evidence and its metrics may be compared.
- `execution_valid: false`: execution ended without a valid experiment. Read
  `failure.source`, `failure.category`, the child run and logs.
- `execution_valid: null`: the plan is still active.
- `failure.source: platform`: submission, resource, runtime, timeout, lease,
  protocol or cancellation failure. This is not evidence that the strategy was
  poor.
- `failure.source: plugin`: the tool/plugin rejected or failed the experiment.
  Domain interpretation belongs to the plugin/Agent, not the kernel.

Automatic retries use the task's `max_attempts` and only apply to failures the
plugin marks retryable. A requested retry creates another attempt; it never
replaces the original evidence.

## Codex handoff prompt

Give a fresh Codex this repository and say:

```text
Read docs/AGENT_OPERATIONS.md and docs/EXECUTION_PLAN_PROTOCOL.md. Use the
execution base for every EDA invocation. Create a plan from the requested
experiment, submit it with tools/verify-plan.py, and report only recorded run
IDs, failure classification, artifact SHA-256 values, metrics and toolchain
evidence. Do not invoke EDA tools directly.
```

The local no-auth profile is intentionally bound to loopback. A remote Agent
should execute on the EDA server or reach loopback through an SSH tunnel; do not
expose this profile on a network interface.
