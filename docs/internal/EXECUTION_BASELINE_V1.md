# Execution Platform v1 baseline

This repository is the execution and management base for Agentic EDA. It is
not an EDA implementation, a data platform, or one of the five agents.

## Boundary

The stable execution envelope is documented in
[`EXECUTION_PROTOCOL_V1.md`](EXECUTION_PROTOCOL_V1.md). Agent and plugin
semantic requests live in the opaque `extensions` namespaces and may evolve
independently of the runtime.

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

The intended local policy is that platform tasks may use at most 60% of the
server's configured capacity. Tasks request hard limits. A request above the
default task limit requires a human approval record; it must still fit inside
the global 60% ceiling. CPU concurrency/capacity and cumulative CPU seconds
are different quantities. The current `cpu_seconds` field does not express
a request for eight CPU cores. The current runtime can
measure and terminate a process tree for per-attempt limits, but it does not
now reserve a global 60% budget atomically at attempt claim time. Approval
records and OS-level cgroup isolation remain future work. Resource scheduling
is local and FIFO in v1; multi-node and multi-user scheduling are later
concerns.

## v1 agent boundary

Agents are independent services using the platform client. A generic process
entry point may later run an agent under the same lifecycle controls, but no
functional agent is part of this repository.

## Acceptance scenarios

The implementation must be exercised end to end for success, missing input,
unknown or unadmitted plugin, launch failure, plugin failure, malformed result,
missing or out-of-workspace artifact, timeout, cancellation, resource breach,
worker loss, retry, duplicate task id, and result queries.

The first acceptance run is observational: record state transitions, workspace
contents, database evidence, logs, and API responses before changing runtime
code.
