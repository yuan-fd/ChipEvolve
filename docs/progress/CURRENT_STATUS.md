# Current status

updated_at: 2026-08-06
phase: P0-P22 completed; awaiting user acceptance

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
- The five-tab English site shows research citations, confidence/OOD reasons,
  human approval, and the second Runtime submission gate. It still owns no EDA
  subprocess.
- P22 added a four-flow demo manifest and non-destructive SQLite backup/restore.
  Nine live state databases were backed up, hash-checked, integrity-checked, and
  restored into a new `/tmp` directory without overwriting source state.
- Full regression: 187 passed, 2 optional `pya` tests skipped, 0 failed. Node
  syntax, Python compile, 41 JSON files, diff whitespace, tracked credential
  patterns, fixed EDACraft commit, and source cleanliness checks passed.
- No push, public deployment, credential read, expensive DPLEvolve run, or
  commercial EDA execution occurred. User-owned untracked `plan/` files remain
  untouched and uncommitted.

Evidence: `P18_EDACRAFT_REAL_ACCEPTANCE.*` and
`P19_P22_PLATFORM_ACCEPTANCE.*` under `docs/evidence/`.
