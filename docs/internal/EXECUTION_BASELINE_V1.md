# Execution Platform v1 baseline

This repository is the execution and management base for Agentic EDA. It is
not an EDA implementation, a data platform, or one of the five agents.

## Boundary

The plugin execution envelope is documented in
[`PLUGIN_PROTOCOL.human.md`](../PLUGIN_PROTOCOL.human.md), and Agent task lists
in [`EXECUTION_PLAN_PROTOCOL.md`](../EXECUTION_PLAN_PROTOCOL.md). Plugin
semantic requests live in opaque `inputs` and `parameters` mappings and may
evolve independently of the runtime.

The platform owns task contracts, input registration and staging, per-attempt
workspaces, queueing, local worker execution, resource policy, process
lifecycle, progress and logs, cancellation, retry requests, artifact
registration, evidence, and query APIs.

Plugins own tool invocation, domain parameters, EDA report interpretation, and
domain quality decisions. Agents own intent, reasoning, and task sequencing.
The data platform owns design and run data indexing. The platform does not
embed any of those domain implementations.

## One task lifecycle

```text
validate/discover/admit -> queued -> preparing -> running
  -> succeeded | failed | cancelled | timed_out | lost
```

Validation and admission are pre-submission checks, not persisted run states.
An allowed retry uses `retry_wait -> queued`; cancellation first records
`cancel_requested`. Attempts have their own statuses and must not be confused
with run statuses.

Each attempt receives a separate workspace directory (not a security sandbox).
Inputs are copied or resolved from
platform artifacts and measured by the platform. The plugin receives a request
file, writes a result file and declared outputs, and exits. The platform
measures output hashes, records logs and progress, and exposes the evidence via
the client/API.

## v1 resource policy

The local policy allows platform tasks to reserve at most 60% of configured
server capacity. CPU-core and memory reservations are claimed atomically in
SQLite when a worker starts an attempt and released at every terminal outcome
or lost lease. Tasks without a request reserve one core and 1 GiB. Process-tree
CPU time, resident memory and process count are measured and can terminate an
attempt after a breach; these are not cgroup hard limits. Scheduling remains
local FIFO; multi-node and multi-user scheduling are later concerns.

## v1 agent boundary

Agents are independent clients. They may submit individual tasks or use the
independent plan service for an ordered task list and artifact handoff. No
reasoning Agent is part of this repository.

## Acceptance scenarios

The implementation must be exercised end to end for success, missing input,
unknown or unadmitted plugin, launch failure, plugin failure, malformed result,
missing or out-of-workspace artifact, timeout, cancellation, resource breach,
worker loss, retry, duplicate task id, and result queries.

The first acceptance run is observational: record state transitions, workspace
contents, database evidence, logs, and API responses before changing runtime
code.
