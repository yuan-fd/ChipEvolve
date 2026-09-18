# Architecture overview

[中文版本](ARCHITECTURE_OVERVIEW.zh-CN.md)

This document is the map between the README and the wire protocol. It explains
where a change belongs, what the platform owns, and what happens to a task from
submission to evidence. It intentionally does not repeat every JSON field;
those rules live in the [Toolkit protocol](PLUGIN_PROTOCOL.human.md).

## The one-sentence model

The Agent owns experiment intent and strategy. The Execution Foundation owns
reliable execution and evidence. A Toolkit owns knowledge of a tool or
toolchain. The foundation does not decide whether placement, routing or timing
analysis is the right next step.

```text
Controller / Flow / ECO / Query Agent
                |
                | plan, task, parameters, scripts, patches
                v
        +----------------------+
        | Execution Foundation |
        | API + plan executor  |
        +----------+-----------+
                   |
                   | request/result files, workspace, lifecycle
                   v
        +----------------------+
        | Toolkit Adapter      |
        | OpenROAD / Innovus / |
        | PrimeTime / custom   |
        +----------+-----------+
                   |
                   v
             EDA tool process
```

The agent can choose a Toolkit, select a capability, provide arbitrary domain
parameters, write a script, or stage a patch. It must not bypass the
foundation to create its own unmanaged tool process or workspace. Workspace
containment and process supervision are execution controls, not an OS sandbox;
Toolkits are trusted code and are not given network or filesystem isolation by
this repository.

## Repository map

### `contracts/`: shared vocabulary

This package contains dependency-light public data contracts: task specs,
runtime results, inputs, artifacts, progress and resource requirements. It
should describe the boundary, not implement a tool flow. Domain-specific
parameters remain mappings and are not interpreted by the kernel.

### `core/runtime/`: the execution kernel

Runtime owns durable run state and the mechanics of one attempt:

- state transitions and SQLite persistence;
- input staging and content digests;
- per-attempt workspace creation;
- worker leases and lost-worker recovery;
- process-tree supervision, timeout and cancellation;
- resource reservation and sampled measurements;
- adapter request/result handling;
- artifact, metric, log and timeline evidence.

Runtime is deliberately unaware of OpenROAD, Innovus, PrimeTime, placement,
CTS, routing and QoR policy. If a new tool requires a new domain branch in this
directory, the boundary is probably wrong.

### `core/registry/`: discovering external capabilities

Registry finds Toolkit manifests, validates their identity and supported
architecture, checks provenance and reads the platform-owned admission record.
It decides whether an external process may be resolved. It does not import the
Toolkit and does not validate the meaning of its domain parameters.

### `core/provenance/`: preserving evidence relationships

Provenance connects a metric to the artifact and parser that produced it. The
platform computes artifact hashes from disk; an adapter's claimed hash is not
trusted as the measurement.

### `core/evaluator/`: a domain boundary

An evaluator can interpret reports and produce domain results, but it runs
through the same external-process boundary. The kernel records and checks the
evidence contract; it does not decide what a good physical-design result is.

### `core/client/`: a small API client

The client is used by applications and acceptance tests to submit and query
runs. It is not a second runtime or a place to duplicate lifecycle logic.

### `gateway/`: composition and HTTP routing

The gateway builds the kernel, authenticates when configured, exposes kernel
routes, and routes application paths. It is an entry point, not a scheduler and
not a Toolkit registry replacement.

### `apps/plan_executor/`: Agent-authored plans

The plan executor persists an ordered list of Agent-authored tasks, submits one
step through the normal kernel API, transfers declared artifacts between steps,
and reports plan state. It does not generate an EDA flow, rewrite parameters or
choose a strategy. The current implementation is serial by design.

### `plugins/` and `examples/`: boundary examples

`plugins/` contains the smallest discovered plugin used by the repository.
`examples/research-toolkit/` is a more instructive external-process example: it
executes an Agent-provided script and performs a patch/build/benchmark task.
Real EDA integrations can be maintained in separate repositories and installed
through the same manifest protocol.

### `guardrails/`: architecture as executable rules

Guardrails prevent vendor names and adapters from leaking into the kernel,
prevent apps from opening kernel databases directly, and ratchet code size and
package boundaries. Negative fixtures are intentionally broken; they must stay
broken so the guardrails prove that they detect the violation.

## Task lifecycle

The normal path is:

```text
1. Agent creates a TaskSpec or ordered Plan
2. API validates the structural contract
3. Plan executor persists the Agent's plan
4. Kernel registers inputs and creates a run
5. Worker reserves resources and creates an attempt workspace
6. Runtime writes adapter_request.json and starts the Toolkit Adapter
7. Adapter invokes the EDA tool and writes adapter_result.json
8. Runtime supervises the process and records logs/progress/timeline
9. Runtime validates declared outputs and hashes artifacts from disk
10. Client/API exposes run, artifacts, metrics, resources and failure evidence
11. Plan executor passes selected artifacts to the next Agent-authored step
```

The plan executor may decide that a later task depends on an earlier artifact,
but it does not interpret the artifact or invent the next task. That remains an
Agent decision.

## Where a new change belongs

| Change | Correct home | Why |
| --- | --- | --- |
| Add a field to the public task/result contract | `contracts/` plus contract tests | It changes the boundary |
| Add timeout, lease, workspace or artifact behavior | `core/runtime/` | It is execution mechanics |
| Add manifest validation or admission behavior | `core/registry/` | It concerns external package discovery |
| Add an API route | `gateway/` or the owning app | HTTP composition belongs at the edge |
| Add plan ordering or artifact binding | `apps/plan_executor/` | Plans are an application concern |
| Invoke a new EDA tool or parse its report | external Toolkit repository | Domain knowledge must not enter the kernel |
| Add a new Toolkit example | `examples/` | Examples should not become platform coupling |
| Enforce an architectural rule | `guardrails/` and its negative fixture | The rule must be executable |

## Boundary decisions and trade-offs

### Why external processes instead of an in-process plugin API?

An external process keeps the Toolkit's interpreter, dependencies and release
cycle independent. It also gives the runtime one lifecycle model for a Python
script, a compiled adapter or a vendor launcher. The cost is a file-based
request/result protocol and explicit installation/admission work.

### Why does the foundation not normalize all capabilities?

`place`, `route`, `timing` and `build` do not mean exactly the same thing across
toolchains. Normalizing them in the kernel would turn the execution layer into
an EDA policy layer and would make new tools conform to the first tool's model.
The foundation records opaque inputs; the Toolkit owns capability semantics.

### Why is the plan executor separate?

It is an Agent-facing application that persists a plan and performs artifact
handoff. Keeping it outside the kernel lets the kernel remain a reliable
single-task execution base and keeps future planning models from becoming
kernel policy.

## Current scope

Verified scope is one host, local state, external processes and serial plans.
Parallel scheduling, multi-node placement, hard cgroup enforcement, remote
object storage, production monitoring and a frozen compatibility protocol are
follow-up work. They should be added only when a concrete experiment requires
them, with a separate contract and acceptance evidence.
