# Current status

updated_at: 2026-08-06
phase: P0-P17 completed

- Workflow Runtime remains the only authority for processes, attempts,
  artifacts, events, and terminal state. The web/API, LLM, BO/GP, RL,
  DPLEvolve, and third-party extensions have no bypass execution path.
- The core platform retains natural-language Spec sessions, RTL import and
  generation, Graphviz netlist schematics, OpenROAD/ORFS RTL-to-GDS,
  stage-aware Campaigns, bounded ReAct recovery, KLayout 2D views, and the
  pinned TaiWei real 3D gcd evidence chain.
- Self-evolution retains provenance-separated public knowledge, observed-only
  collection, RAG, multi-objective BO/GP, Pareto evidence, RL shadow advice,
  user accept/modify/reject decisions, and a fail-closed T2 eligibility gate.
- P17 replaces the product-level “IC Craft” framing with an optional EDACraft
  Extension Pack containing six separate plugins: RTLCraft, EDACode,
  TCADCraft, MoMCraft, CktCraft, and the preserved ImplCraft adapter.
- Six P17 Runtime acceptance runs succeeded. RTLCraft emitted real upstream DSL
  RTL; TCADCraft ran upstream geometry; MoMCraft ran upstream Touchstone I/O;
  EDACode/CktCraft remained explicit constrained/source audits; ImplCraft
  generated three Tcl scripts without commercial EDA.
- The website now has exactly five English tabs: Overview, Frontend Design,
  Backend Design, Projects & Results, and Self-Evolution. It uses a warm,
  minimal visual system and displays store-backed data with honest empty states.
- `apps/api/services/platform_service.py` now owns the cross-page read model;
  `app.py` stays the dependency-free HTTP/routing shell and does not become a
  second scheduler.
- P17 full regression: 182 tests. Node syntax, Python compilation, JSON,
  diff-whitespace, credential-pattern, plugin-lock, and directory checks pass.
- User `plan/` documents, historical failure evidence, `var/`, `.tools/`, and
  `.external-src/` were preserved. Project-owned Python/pytest caches were
  removed. No push, deployment, credential read, or credential commit occurred.

Truth boundaries: P17 did not claim full TCAD convergence, an EM solve,
CktCraft numerical simulation, EDACode LLM execution, commercial EDA, or an
expensive DPLEvolve flow. Those remain future capability promotions.
