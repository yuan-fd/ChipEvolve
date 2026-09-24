# Execution-base capability gap matrix

| Capability | Current state | Gap / impact | Priority | Next repair slice |
| --- | --- | --- | --- | --- |
| Agent task list | Ordered serial plans and artifact bindings work | No general DAG or parallel scheduling | P1 | Define dependency and concurrency contract before implementation |
| Agent authority | Opaque `inputs`/`parameters` pass through; scripts and patches can be staged | Toolkit/capability terminology and script/patch examples were previously implicit | P0 | Keep agent strategy outside the kernel; maintain Toolkit execution examples |
| Input intake | Host-path, artifact, and bounded local upload staging with measured digests | No remote object store; upload is single-host and request-size bounded | P2 | Keep remote storage out of the kernel until a real deployment requires it |
| Design identity | `project_id` and `design_id` are validated labels | No immutable design version or content identity | P0 | Add design/input manifest identity at the data boundary |
| Attempt workspace | Per-run, per-stage, per-attempt directory with containment checks | No design-level workspace and retention policy | P1 | Define lifecycle, ownership and cleanup contract |
| Artifact/provenance | Hashes, manifests, metrics and artifact graph exist | No quota, retention or remote object store | P1 | Specify retention and storage lifecycle before code |
| Toolkits/adapters | External package discovery, admission, process envelope and opaque capabilities work | Toolkit metadata and cross-machine preflight need a stable authoring guide | P1 | Define Toolkit manifest guidance without adding a second wire protocol |
| Resource admission | SQLite reservations and sampled CPU/memory/process limits | No cgroup hard limits or multi-node placement | P1 | Decide host isolation and scheduler boundary |
| Process lifecycle | Lease, cancellation, timeout, lost attempt and bounded logs exist | Resource enforcement is sampled, not hard | P1 | Add OS enforcement behind an explicit host capability contract |
| Failure handling | Platform/plugin categories, bounded retry state, and plan `retry_wait` for transient kernel outage | Retry metadata is local to the plan executor process | P1 | Persist retry budget/timing if a long-lived service requires restart recovery |
| API | Run, artifact, metric, resource, timeline and plan routes exist | Protocol is `v1alpha1`; compatibility negotiation is incomplete | P0 | Freeze task/result/error protocol after acceptance evidence |
| Observability | Durable state and query APIs expose execution evidence | No production monitoring/alerting contract | P2 | Define health, metrics and retention signals |
| Architecture gates | 40 guardrail tests pass; G7/G8 pass; app smokes run in pytest | G4/G5 are syntactic; G10 has no protected production hashes | P0 | Keep CI quality gate green; add protected hashes after the production file set is frozen |
| Data底座接口 | Artifact and metric evidence can be queried | Design-state indexing is outside this repository | P1 | Define handoff schema with the data platform |

## Boundary decisions

The kernel remains domain-neutral. ORFS, OpenROAD, Yosys and KLayout stay in
the external Toolkit package. An Agent may choose Toolkit capabilities, supply
parameters, scripts, or patches, and compose tasks; it must not bypass the
execution foundation to operate a tool. Design parsing, QoR interpretation and
agent reasoning remain outside the execution kernel.

The current verified target is a single-host, serial task list with local
SQLite/filesystem state. Parallel plans, multi-node scheduling, hard isolation,
retention and remote objects are explicit follow-up capabilities rather than
implicit behavior.

## 2026-09-23 implementation update

The first correctness slice is now implemented: attempts record process identity
and stale lease recovery fences the recorded process group before releasing
resources; admission and process monitoring use the same effective default
resource request; optional host input roots are enforced; artifact references
are checked against the owning run; and the plan service can require caller
authentication and owner checks. The kernel health view reports worker presence
and queue age. Run evidence can carry design revision, experiment and a
platform-computed input manifest digest, and authorized clients can download a
hash-verified artifact within the gateway response limit.

The remaining gaps are unchanged: sampled resource monitoring is not an OS hard
limit, the plan service still executes an ordered serial list, the complete
Design→Revision→Experiment data model and retention contract are not present,
and protocol freezing, remote objects and multi-node scheduling remain future
work.

## 2026-09-24 implementation update

The failure path now preserves the platform-owned execution request, result,
log, input manifest and protocol receipt when those files exist. They are
captured as hash-verified runtime evidence, so a failed EDA attempt keeps a
downloadable scene for diagnosis even when the Toolkit produced no domain
artifact. The effective resource request also now carries CPU-time and
process-count limits through to the guardian instead of dropping them.

The Query-Agent boundary is intentionally read-only and lightweight: it turns
questions into authorized foundation queries and explains sourced results. It
does not execute commands, open the kernel database, parse EDA files or make
QoR decisions; those responsibilities stay with the foundation and Toolkits.

## 2026-09-24 scope decision

The deployment target is one shared server. Remote object storage, multi-node
workers, per-user quotas, fair scheduling, preemption and complex DAG planning
are explicitly not required. The minimum product contract is a bounded queue,
reliable execution, ownership checks, and a transparent local evidence tree.

The foundation now exposes `Design → Revision → Run → Attempt/State →
Artifact/Metric` through a design tree query. It can also create a local ZIP
bundle containing the run state, events, staged input bytes, hash-verified
artifacts and their index. Large bundles remain available through the existing verified artifact
chunk/download interface; no second storage system is introduced.
