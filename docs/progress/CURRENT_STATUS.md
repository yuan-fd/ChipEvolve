# Current status

updated_at: 2026-08-07
phase: P0-P22 completed; five-page workflow-clarity revision awaiting user acceptance

- Workflow Runtime remains the only authority for process launch, attempts,
  events, artifacts, recovery, and terminal state. Web, LLM, BO/GP, RL, RAG,
  EDACraft, TaiWei, and DPLEvolve have no bypass path.
- The core platform provides multi-turn natural-language Spec review, RTL
  import/generation, netlist schematics, OpenROAD/ORFS RTL-to-GDS, stage-aware
  Campaigns, bounded ReAct repair, 2D layout, and pinned TaiWei real 3D evidence.
- P18 promoted CktCraft to a real converged `.op` solve and MoMCraft to a real
  one-frequency coarse numerical microstrip solve. Both are optional, hashed,
  Runtime-owned, and explicitly non-sign-off.
- TCADCraft now executes real upstream geometry and physics invariants. Its
  pinned full solver has an upstream header/source mismatch, preserved in the
  environment lock; no full convergence claim is made.
- EDACode now emits a review-only proposal artifact with an empty executable
  tool registry. RTLCraft and the preserved ImplCraft integration remain intact.
- P19 maps four cited papers to actual BO/Pareto, evidence RAG, offline RL, and
  GP code with DOI and execution-boundary traceability.
- P20 adds bounded deterministic benchmark generation, leave-one-out GP
  calibration, empirical interval coverage, residual scale, and explicit OOD
  checks. Plans/predictions never enter the observed store.
- P21 connects accept/modify/reject decisions to an idempotent ExperimentPlan
  and Campaign. Runtime submission is a separate confirmation. Verified
  terminal metrics can be collected into both tenant learning and the matching
  optimization study as observed-only evidence.
- The site now has five primary pages: Overview, Frontend, Backend,
  Projects & Results, and Self-Evolution. The standalone Extensions page was
  removed; optional capabilities open inside the relevant workspace.
- Frontend provides eight audited examples from simple logic through ALU,
  controller, UART, and mini RISC-V. Yosys produces the real netlist; Graphviz
  renders bounded connectivity and large designs use a synthesized cell/port
  overview instead of hand-drawn circuits.
- DPLEvolve remains an optional, user-configured long task inside the Backend
  research branches. It is not started by the website and remains outside the
  primary RTL-to-GDS path.
- The visual system now follows modern efficiency-tool minimalism: neutral
  surfaces, one blue action color, sans-serif hierarchy, compact spacing, small
  radii, visible hairlines, and no decorative card shadows. Optional long-task
  dashboards appear only after the matching extension is selected.
- Frontend RTL and netlist outputs now use a white, dark-text, line-numbered,
  wrapped viewer. Browser-only formatting expands minified HDL without changing
  the registered or downloaded source.
- RTLScout now has an explicit configure/connect/submit/monitor/evidence path,
  an evidence-backed run dashboard, and an audited bounded offline demo. The
  provider button only connects a memory-only session profile; it cannot start
  execution, and BYOK remains disabled on the external HTTP review service.
- RTLCraft, EDACode, and ImplCraft remain available as adapters and historical
  evidence but are no longer duplicated in the user-facing main flow. Only the
  complementary TCADCraft, MoMCraft, and CktCraft branches are exposed.
- Flow-Agent is no longer presented as a separate product. Stage-aware and
  Agent-guided behavior is exposed through Backend flow modes, while internal
  orchestration remains behind the interface.
- Overview now contains an eight-step end-to-end tutorial covering model-backed
  Spec-to-RTL, verified RTL exploration, optional device/circuit smokes,
  baseline 2D GDS, stage-aware batches, Agent-guided plans, TaiWei 3D, and
  explicit verified-experience collection. API-key requirements are shown per
  step.
- Frontend and Backend now use the user-supplied interactive-demo pages as the
  structural reference: one narrow vertical workspace, numbered gray panels,
  visible input choices, controls next to actions, vertical stage status, and
  evidence directly below the producing flow.
- Backend business semantics are explicit: Baseline queues one Runtime run;
  Campaign and Agent modes create three bounded unbound candidates for review.
  They never execute automatically from the Web process.
- A persistent `中文 / EN` header switch has a translation entry for every
  marked static interface string. Technical names remain stable; review links
  may use `?lang=zh` or `?lang=en`.
- Browser startup no longer selects the newest design, physical run, or
  RTLScout record. Users make an explicit selection, and primary labels use
  `Design 01` / `Run 01`; authoritative hashes remain available only in raw
  evidence and artifact integrity fields.
- A dedicated Workflow Runtime worker now consumes the same durable queue used
  by Web/API submissions. A heartbeat is surfaced in `/api/health` and in the
  Backend run panel. Six stale queued records were preserved and cancelled;
  a new bounded TCADCraft smoke advanced queued -> running -> succeeded.
- P22 added a four-flow demo manifest and non-destructive SQLite backup/restore.
  Nine live state databases were backed up, hash-checked, integrity-checked, and
  restored into a new `/tmp` directory without overwriting source state.
- Workflow-clarity regression: 198 passed, 0 failed. Node
  syntax, Python compile, 41 JSON files, diff whitespace, tracked credential
  patterns, fixed EDACraft commit, and source cleanliness checks passed.
- No push, public deployment, credential read, expensive DPLEvolve run, or
  commercial EDA execution occurred. User-owned untracked `plan/` files remain
  untouched and uncommitted.

Evidence: `P18_EDACRAFT_REAL_ACCEPTANCE.*` and
`P19_P22_PLATFORM_ACCEPTANCE.*` under `docs/evidence/`; the information-
architecture and modern-minimal visual revisions are recorded in
`P22_UI_REVISION_ACCEPTANCE.*`, `P22_MINIMAL_UI_ACCEPTANCE.*`, and
`P22_FRONTEND_WORKSPACE_ACCEPTANCE.*`. The latest structural and language
revision is recorded in `P22_REFERENCE_WORKSPACE_BILINGUAL_ACCEPTANCE.*` and
`P22_WORKFLOW_CLARITY_ACCEPTANCE.*`.
