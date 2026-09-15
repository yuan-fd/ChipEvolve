# Execution base audit

## Scope

This audit covers the execution and management base only. The data platform,
the five agents, and EDA semantics are outside this repository's scope.

## Evidence collected

| Area | Result | Evidence |
| --- | --- | --- |
| Platform test suite | Pass | `419 passed, 1 skipped` (including input API and concurrent idempotent submission regression) |
| Guardrails | Pass | `39 passed` |
| Input, resource, worker lifecycle | Pass | `83 passed, 1 skipped` |
| Minimal external plugin | Pass | queued -> succeeded; workspace, events and artifact hashes inspected |
| edair external integration | Pass | `4 passed` against this checkout |
| ORFS platform integration | Pass | `6 passed` with the prepared toolchain |
| ORFS real finish/evaluator | Pass | `7 passed` including the real six-stage flow and protected evaluation |
| vulture on formal code | Pass | no findings after removing an unused signal-handler parameter |
| ruff/black on formal code | Open | ruff findings remain; Black reports 34 files needing formatting |

## Capability status

Implemented and exercised:

- task validation and idempotent submission;
- plugin discovery, admission and external-process execution;
- per-attempt workspaces and input staging from host files or artifacts;
- platform-owned input registration from configured roots, with content-addressed storage and ownership checks;
- queue, worker leases, cancellation, timeout, retry budget and lost-attempt recovery;
- progress events and process output capture (the per-poll drain is bounded;
  the queue and total log size are not bounded);
- artifact allowlists, workspace containment and platform-computed hashes;
- metric-to-artifact provenance;
- local process-tree CPU, memory and process-count measurement;
- client/API queries for runs, events, artifact excerpts and metrics;
- concurrent idempotent submission is serialized by the store transaction;
- architectural guardrails preventing plugin knowledge from entering the kernel.

Implemented only as a target or partially implemented:

- The 60% server-wide resource budget is enforced by atomic SQLite reservations
  at attempt claim time, released on terminal or lost attempts. Approval records
  and OS-level cgroup isolation remain unimplemented.
- Resource limits are sampled and terminate an attempt after a breach. They
  are not OS-level cgroup/container hard limits.
- Plugin revision checking compares declared commit values. A complete digest
  lock of the executable plugin tree or release artifact is not enforced.
  The agreed priority is plugin conformance CI, rather than introducing a new
  runtime locking system. CI results should identify the code tested; current
  metadata checks must not be described as executable-content verification.
- Storage is local SQLite/filesystem. Retention, quota, object storage and
  lifecycle cleanup are not defined as a production contract.
- Scheduling is local FIFO. There is no multi-node backend or resource-aware
  placement.
- Protocol is `v1alpha1`; compatibility negotiation and a frozen v1 release
  are not complete.
- The public API has no explicit retry endpoint, live adapter-log endpoint, or
  resource-usage endpoint. Input registration now exists for local files under
  configured roots and artifact references; byte upload and remote object
  sources remain future work. Reading a
  declared log artifact is different from querying a running process's log.
  Existing retry tests exercise the runtime's preconfigured attempt budget,
  not an Agent requesting a retry through the public API.
- G10 has a negative fixture, but `protected_sha256` is empty in the baseline.
  A green test therefore does not prove any production file is hash-locked.

## Required next work

1. Run the two skipped ORFS toolchain tests with the prepared ORFS/OpenROAD/Yosys
   environment and retain the command output as evidence.
2. Add a small, explicit global resource policy: configured host capacity,
   60% platform ceiling, per-task reservation, FIFO wait when unavailable, and
   a human approval record for exceptional requests.
3. Establish plugin conformance CI with an identifiable tested revision,
   success/failure cases and the platform protocol checker.
4. Clean formal-code lint and formatting in small module-scoped changes; do
   not alter negative guardrail fixtures merely to make static tools green.
5. Freeze the minimal task/result/input/attempt/error protocol after the real
   Agent task-list run has been observed end to end.
6. Close the public API gaps for requested retries, running logs and resource
   usage. Extend input sources only through the existing InputReference shape.
   Keep domain logic in independent Agents/plugins.

## Boundary decision

No EDA parser, tool-specific branch, agent workflow, data-indexing logic,
marketplace, or MCP-specific business logic belongs in this execution base.
