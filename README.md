# OpenROAD Platform v2

A domain-neutral execution base for Agentic EDA. Agents decide what to run;
this platform owns durable runs, attempt workspaces, resource reservations,
process lifecycle, retries, logs, artifacts, metrics and provenance.

The kernel contains no OpenROAD or ORFS logic. EDA integrations are external
plugins selected by `plugin_id`. Ordered task lists are owned by the independent
`plan_executor` application and use the kernel's normal task API.

## Fresh-server quick start

Requirements: Linux, Bash, Python 3.9 or newer, and an external plugin checkout.
For ORFS, the server also needs an ORFS checkout and executable OpenROAD, Yosys
and KLayout builds.

```bash
git clone <platform-repository-url> openroad-platform-v2
cd openroad-platform-v2
tools/install-local.sh

export OPENROAD_PLATFORM_PLUGINS_ROOT=/srv/agenticeda-orfs
export OPENROAD_PLATFORM_STATE_ROOT=/srv/openroad-platform-state
tools/start-platform.sh
```

The launcher binds the unauthenticated development surface to `127.0.0.1`
only. It starts the gateway (`8700`), worker and plan service (`8840`) as one
foreground process group.

In another shell, edit the absolute server paths in the example and submit it:

```bash
cp examples/orfs-gcd-plan.json /tmp/orfs-gcd-plan.json
.venv/bin/python tools/verify-plan.py /tmp/orfs-gcd-plan.json
```

The verifier waits for a terminal result and prints the plan, child run,
artifact hashes, environment snapshot, metrics, resources and a bounded log
excerpt as JSON. Exit status is zero only when `execution_valid` is true.

Agent operators should read [Agent operations](docs/AGENT_OPERATIONS.md) and
the [execution-plan protocol](docs/EXECUTION_PLAN_PROTOCOL.md). Plugin authors
should use [the plugin protocol](docs/PLUGIN_PROTOCOL.agent.md).

## Repository boundary

- `contracts/`: dependency-free public data contracts.
- `core/`: runtime, registry, provenance, identity, evaluator and client.
- `gateway/`: localhost HTTP composition root.
- `apps/plan_executor/`: durable serial plans and artifact handoff.
- `plugins/`: only the minimal example plugin; real EDA plugins stay external.
- `guardrails/`: executable architecture constraints.

Out of scope for this local baseline: multi-node scheduling, multi-user
isolation, EDA semantics in the kernel, and a general DAG language.
