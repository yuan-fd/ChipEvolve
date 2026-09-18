# Agentic EDA Execution Foundation

[中文说明](README.zh-CN.md)

This repository is the execution foundation for Agentic EDA. An agent decides
what experiment is worth running. The foundation turns that decision into a
durable, workspace-contained and inspectable run. A Toolkit Adapter knows how
to invoke a particular EDA tool or toolchain.

The important boundary is simple:

```text
Agent
  -> intent, capability, parameters, scripts, code or patches
Execution Foundation
  -> workspace, process, resources, lifecycle, evidence and provenance
Toolkit Adapter
  -> tool commands, environment, parsers and artifact extraction
EDA tool
```

The agent is not reduced to a fixed flow. It may choose a Toolkit capability,
compose tasks, generate a TCL or Python script, provide a source patch, or
decide that a low-level script is more useful than a prebuilt capability. The
constraint is that the actual tool process is still started and recorded by the
Execution Foundation.

## What this project does

For every task or plan, the platform can:

- stage inputs and record their SHA-256 digests;
- create an attempt workspace;
- start an external Toolkit process;
- enforce timeout, cancellation, leases and bounded retry;
- record logs, progress, resources and state transitions;
- register artifacts and metrics with provenance;
- classify platform, Toolkit and tool failures;
- expose the evidence through the kernel API and the Agent plan API.

The current target is deliberately modest: one host, serial agent-authored task
lists, local SQLite/filesystem state, and external Toolkit processes. It is not
a Kubernetes scheduler, a general DAG engine, or an EDA strategy planner.

## Why it exists

EDA state is spread across RTL, netlists, DEF/GDS/ODB files, reports, logs,
tool databases and metrics. An agent can reason about an experiment, but it
should not have to rebuild workspace management, process supervision, artifact
copying and failure bookkeeping for every tool invocation. This repository
keeps those engineering duties in one place while leaving EDA decisions to the
agent and domain interpretation to the Toolkit or evaluator.

## A quick path to a real run

The supported local setup is Linux, Bash and Python 3.9 or newer. Start with a
clean checkout:

```bash
git clone <repository-url> openroad-platform-v2
cd openroad-platform-v2
tools/install-local.sh
# The installer only installs platform packages. Add the test runner when it
# is not already available in your development environment.
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest -q
```

For a real ORFS run, install or clone the external ORFS Toolkit and provide its
ORFS, OpenROAD, Yosys and KLayout paths. The platform repository intentionally
does not vendor those tools. The local runbook is
[`docs/AGENT_OPERATIONS.md`](docs/AGENT_OPERATIONS.md); the complete GCD
acceptance record is
[`docs/internal/GCD_ACCEPTANCE_2026-09-18.md`](docs/internal/GCD_ACCEPTANCE_2026-09-18.md).

The launcher is for local development and binds to `127.0.0.1`. Do not expose
the `--no-auth` development profile on a network interface. For a team server,
use the team's access control, SSH/VPN boundary or a reviewed reverse proxy.
The workspace and process controls are lifecycle boundaries, not an operating
system sandbox: a Toolkit must be trusted and may still have access to the
host environment allowed by its deployment.

## Repository map

The short version is in
[`docs/ARCHITECTURE_OVERVIEW.md`](docs/ARCHITECTURE_OVERVIEW.md). The main
directories are:

| Directory | Responsibility |
| --- | --- |
| `contracts/` | Dependency-light task, result, input, artifact and resource contracts |
| `core/runtime/` | Durable state, workspace attempts, worker, process and resource lifecycle |
| `core/registry/` | External Toolkit discovery, manifest validation and admission |
| `core/provenance/` | Artifact and metric evidence relationships |
| `core/evaluator/` | Boundary for domain-owned evaluation results |
| `core/client/` | Small client for the public kernel API |
| `gateway/` | HTTP composition root and API routing |
| `apps/plan_executor/` | Agent-authored ordered plans and artifact handoff |
| `plugins/` | Minimal in-repository example; real EDA Toolkits may live elsewhere |
| `guardrails/` | Executable architecture rules and deliberate negative fixtures |

## How to add a Toolkit

A Toolkit is an external process package, not a Python import into the kernel.
It supplies a manifest, an adapter entry point and artifact rules. The adapter
accepts `--request <path>` and `--result <path>`, runs its tool in the workspace,
and writes the protocol result. The platform measures artifact hashes itself.

Start from the working example in
[`examples/research-toolkit/`](examples/research-toolkit/), then read:

1. [`docs/TOOLKIT_ARCHITECTURE.md`](docs/TOOLKIT_ARCHITECTURE.md) for the
   boundary and design rationale;
2. [`docs/PLUGIN_PROTOCOL.human.md`](docs/PLUGIN_PROTOCOL.human.md) for the
   implementation contract;
3. [`CONTRIBUTING.md`](CONTRIBUTING.md) for the step-by-step workflow;
4. [`docs/FAQ.md`](docs/FAQ.md) for common integration failures.

The wire identity is still called `plugin_id` for compatibility. In the
architecture, it is the identity of the external Toolkit package. Capability
names and their parameters remain opaque to the foundation.

This checkout also contains a minimal Cadence Innovus Toolkit under
[`plugins/cadence-innovus/`](plugins/cadence-innovus/). It exposes `preflight`
and Agent-staged `script` capabilities. Supply the deployment's resolved
`tool_path` and module name in task inputs; the adapter captures tool version,
runtime output and script receipt as evidence. Deployment-specific licensing is
not a Toolkit gate or a manifest field. The adapter deliberately does not store
license endpoints or machine-specific paths in the repository. The same adapter
can be reviewed and run through the normal worker lifecycle, so a commercial
tool process is never launched directly by an Agent.

## Current evidence and limits

The repository currently verifies the following:

- the complete Agent Plan -> kernel -> worker -> external ORFS Toolkit -> GCD
  `finish` path;
- current-server runtime/script evidence for Innovus, ICC2, PrimeTime and Genus,
  recorded in [`docs/internal/SERVER_TOOLKIT_MATRIX.md`](docs/internal/SERVER_TOOLKIT_MATRIX.md);
- Agent-authored script, patch, build and benchmark tasks;
- cross-Toolkit artifact handoff;
- cancellation, timeout, retry, lost-worker and idempotency behavior;
- `438 passed, 1 skipped` in the full test suite and `39 passed` guardrails at
  the latest verification.

The protocol remains `v1alpha1`. Cross-machine Toolkit preflight, immutable
design identity, multi-user isolation, hard OS resource limits, remote object
storage and multi-node scheduling are documented follow-up work, not hidden
features.

## Documentation

| Audience | Start here |
| --- | --- |
| New reader, advisor or project owner | This README or [中文 README](README.zh-CN.md) |
| Developer integrating a Toolkit | [Architecture overview](docs/ARCHITECTURE_OVERVIEW.md) |
| Toolkit author | [Plugin/Toolkit protocol](docs/PLUGIN_PROTOCOL.human.md) |
| Contributor | [Contributing guide](CONTRIBUTING.md) |
| Person running an example | [Research Toolkit example](examples/research-toolkit/README.md) |
| Troubleshooting | [FAQ](docs/FAQ.md) |

## License

See [`LICENSE`](LICENSE).
