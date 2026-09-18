# Execution-base capability gap matrix

| Capability | Current state | Gap / impact | Priority | Next repair slice |
| --- | --- | --- | --- | --- |
| Agent task list | Ordered serial plans and artifact bindings work | No general DAG or parallel scheduling | P1 | Define dependency and concurrency contract before implementation |
| Agent authority | Opaque `inputs`/`parameters` pass through; scripts and patches can be staged | Toolkit/capability terminology and script/patch examples were previously implicit | P0 | Keep agent strategy outside the kernel; maintain Toolkit execution examples |
| Input intake | Host-path and artifact staging with measured digests | No upload or remote object source | P2 | Extend `InputReference` only for a concrete storage requirement |
| Design identity | `project_id` and `design_id` are validated labels | No immutable design version or content identity | P0 | Add design/input manifest identity at the data boundary |
| Attempt workspace | Per-run, per-stage, per-attempt directory with containment checks | No design-level workspace and retention policy | P1 | Define lifecycle, ownership and cleanup contract |
| Artifact/provenance | Hashes, manifests, metrics and artifact graph exist | No quota, retention or remote object store | P1 | Specify retention and storage lifecycle before code |
| Toolkits/adapters | External package discovery, admission, process envelope and opaque capabilities work | Toolkit metadata and cross-machine preflight need a stable authoring guide | P1 | Define Toolkit manifest guidance without adding a second wire protocol |
| Resource admission | SQLite reservations and sampled CPU/memory/process limits | No cgroup hard limits or multi-node placement | P1 | Decide host isolation and scheduler boundary |
| Process lifecycle | Lease, cancellation, timeout, lost attempt and bounded logs exist | Resource enforcement is sampled, not hard | P1 | Add OS enforcement behind an explicit host capability contract |
| Failure handling | Platform/plugin categories and bounded retry state exist | Kernel outage visibility and retry policy need richer persistence | P1 | Persist availability errors and retry budget in plan state |
| API | Run, artifact, metric, resource, timeline and plan routes exist | Protocol is `v1alpha1`; compatibility negotiation is incomplete | P0 | Freeze task/result/error protocol after acceptance evidence |
| Observability | Durable state and query APIs expose execution evidence | No production monitoring/alerting contract | P2 | Define health, metrics and retention signals |
| Architecture gates | 39 guardrail tests pass; G7/G8 pass | G4/G5 are syntactic; G10 has no protected production hashes | P0 | Strengthen gates with explicit contract tests and hash set |
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
