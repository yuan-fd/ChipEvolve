# Execution base audit

## Scope

This audit covers the execution and management base only. The data platform,
the five agents, and EDA semantics are outside this repository's scope.

## Evidence collected

| Area | Result | Evidence |
| --- | --- | --- |
| Platform test suite | Pass | Full pytest suite, including ordered plans and public query regressions |
| Guardrails | Pass | `40 passed` |
| Input, resource, worker lifecycle | Pass | `83 passed, 1 skipped` |
| Minimal external plugin | Pass | queued -> succeeded; workspace, events and artifact hashes inspected |
| edair external integration | Pass | `4 passed` against this checkout |
| ORFS platform integration | Pass | `6 passed` with the prepared toolchain |
| ORFS real finish/evaluator | Pass | `7 passed` including the real six-stage flow and protected evaluation |
| vulture on formal code | Pass | no findings after removing an unused signal-handler parameter |
| ruff/mypy on touched runtime and plan code | Pass | No findings in the verified change set |

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
- The public API exposes requested retry, bounded live log, resource-usage,
  artifact, metric, timeline and artifact-excerpt queries. Input registration
  supports local files under configured roots and artifact references; byte
  upload and remote object sources remain future work.
- G10 has a negative fixture, but `protected_sha256` is empty in the baseline.
  A green test therefore does not prove any production file is hash-locked.

## Required next work

1. Establish plugin conformance CI with an identifiable tested revision,
   success/failure cases and the platform protocol checker.
2. Freeze the minimal task/result/input/attempt/error protocol after the real
   Agent task-list run has been observed end to end.
3. Extend input sources only through the existing InputReference shape when a
   concrete remote-object requirement exists. Keep domain logic in independent
   Agents/plugins.

## Boundary decision

No EDA parser, tool-specific branch, agent workflow, data-indexing logic,
marketplace, or MCP-specific business logic belongs in this execution base.

## 2026-09-18 audit update

The current checkout was audited from `95efe5579a8bf211d00e87195e57c54659368a49`.
Existing dirty and untracked files were preserved. The full platform suite
passed with `429 passed, 1 skipped`; all `39` guardrail tests passed; mypy passed
for formal source; and the real ORFS GCD public-API acceptance passed in
157.53 seconds. The detailed evidence is in
[`GCD_ACCEPTANCE_2026-09-18.md`](GCD_ACCEPTANCE_2026-09-18.md).

The independent correctness review identified and the implementation repaired
workspace-creation reservation leaks, stale resumable result files, premature
plan cancellation, plan task validation, broad evaluator exception handling,
and silent corrupt failure records. Regression tests cover workspace failure and
delayed cancellation.

The independent architecture review used change-context and evidence-oriented
methods from Qodo PR-Agent (The-PR-Agent/pr-agent revision
`f7da79cfa061ebb61fb4c109794f94be90ef4b77`) and Bito Examples (gitbito/Examples
revision `9e4d2b1dd2fc0a8e3b075ee94bfc1144f39f2b52`). Neither project was added
as a runtime dependency and no local source was uploaded. The review found that
design identity is still a label, plugin executable-tree locking is incomplete,
storage lifecycle is unspecified, and G4/G5/G10 need stronger enforcement.

Static checks require a precise interpretation. Mypy is clean for the formal
source, and Ruff autofix removed the high-confidence unused-import, import-order
and annotation issues in the touched modules. Fifteen remaining Ruff findings
are historical export-order and import-style findings in untouched package
facades, plus expected security-rule findings; they are recorded rather than
hidden. Black reports historical layout differences; applying it globally
expands core files enough to violate the repository's G7/G8 size ratchet, so it
was deliberately not applied wholesale. Bandit reports zero high-severity
findings; its medium/low findings are the expected subprocess, URL client and
SQL migration/query construction sites and are recorded for a later
boundary-specific review. Vulture findings are only the four deliberate
`guardrails/negative` fixtures and were retained.

## 2026-09-18 final verification update

After the earlier audit snapshot, the independent review found three execution
correctness gaps and the checkout now contains their regression tests and fixes:

- a cancellation request could be overwritten by the submit-to-running update;
- cancelling before the first submission could leave later plan steps pending;
- reusing an idempotency key for a different task was reported as an internal
  server error instead of a conflict.

The fixes preserve `cancel_requested` across the submission race, mark every
unsuccessful step cancelled when a plan is cancelled before submission, and
return HTTP 409 for an idempotency conflict while retaining HTTP 500 for an
unrelated runtime-store failure.

Final verification for the current working tree:

- full suite: `481 passed, 1 skipped`;
- Guardrails: `40 passed`;
- application smoke processes are included in the default suite;
- Ruff: passed for the touched implementation and regression tests;
- mypy: passed for `contracts/src`, `core/*/src`, `gateway/src` and
  `apps/*/src`.

The skipped test is the deliberate negative branch for hosts that cannot
measure a process tree; this host supports that measurement. These results do
not turn the historical repository-wide Ruff/Black debt into a clean-slate
claim, and they do not resolve the documented multi-user idempotency ownership
boundary.

## 2026-09-24 implementation verification

The execution base now preserves platform-owned failure evidence (request,
result, log, input manifest and protocol receipt when present) as hash-checked
artifacts. The effective resource request also carries CPU-time and process
count limits through to the process guardian. Query-Agent remains a thin,
read-only client-side evidence navigator; it does not execute EDA commands or
interpret design results.

Verification for this increment: `496 passed, 1 skipped`; Guardrails `40
passed`; Ruff passed for all touched implementation packages; and the
Query-Agent end-to-end smoke passed. The remaining gaps listed above—hard OS
isolation, full Design→Revision→Experiment persistence, retention/GC,
protocol freeze/conformance CI, and multi-node scheduling—remain explicitly
out of scope for this increment.
