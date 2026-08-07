# Current status

updated_at: 2026-08-07
phase: P0-P22 completed; frontend workspace clarity revision awaiting user acceptance

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
- The six-tab English site now has a formal Overview, vertical Frontend and
  Backend workflows, a dedicated clickable Extensions catalog, vertical project
  index with per-project detail, and a visual Self-Evolution feedback loop.
- Frontend provides eight audited examples from simple logic through ALU,
  controller, UART, and mini RISC-V. Yosys produces the real netlist; Graphviz
  renders bounded connectivity and large designs use a synthesized cell/port
  overview instead of hand-drawn circuits.
- DPLEvolve is presented as an optional, user-configured long task with a run
  dashboard. It is not started by the website and remains outside the primary
  RTL-to-GDS path.
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
- RTLCraft and EDACode are shown as independent frontend alternatives. Their
  links reliably open the selected Extensions detail even if the catalog was
  still loading when clicked.
- P22 added a four-flow demo manifest and non-destructive SQLite backup/restore.
  Nine live state databases were backed up, hash-checked, integrity-checked, and
  restored into a new `/tmp` directory without overwriting source state.
- Frontend-clarity regression: 193 passed, 0 failed. Node
  syntax, Python compile, 41 JSON files, diff whitespace, tracked credential
  patterns, fixed EDACraft commit, and source cleanliness checks passed.
- No push, public deployment, credential read, expensive DPLEvolve run, or
  commercial EDA execution occurred. User-owned untracked `plan/` files remain
  untouched and uncommitted.

Evidence: `P18_EDACRAFT_REAL_ACCEPTANCE.*` and
`P19_P22_PLATFORM_ACCEPTANCE.*` under `docs/evidence/`; the information-
architecture and modern-minimal visual revisions are recorded in
`P22_UI_REVISION_ACCEPTANCE.*`, `P22_MINIMAL_UI_ACCEPTANCE.*`, and
`P22_FRONTEND_WORKSPACE_ACCEPTANCE.*`.
