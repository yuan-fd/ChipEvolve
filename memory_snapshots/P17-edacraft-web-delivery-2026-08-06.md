# P17 milestone: EDACraft extension pack and platform delivery

captured_at: 2026-08-06
status: completed
base_commit: `35ef56d`

## Durable decisions

- Product naming is **EDACraft Extension Pack**, not “IC Craft”.
- EDACraft has six independent plugins: RTLCraft (frontend), EDACode
  (frontend), TCADCraft (device), MoMCraft (interconnect), CktCraft (circuit),
  and ImplCraft (backend).
- `edacraft-implcraft@1.0.0`, its adapter, and its P11 evidence remain intact.
  The P16 `craft_*` API names remain compatibility aliases for the
  backend-neutral digital FlowPlan.
- EDACraft, TaiWei 3D, and DPLEvolve are optional extensions. The core
  Spec→RTL→OpenROAD GDS and evidence-learning loop cannot depend on them.
- Fixed EDACraft source is commit
  `739eee0f3ced8fc3cbb6f01b6cc89414758fd898`; the root license is MIT-like
  with a non-commercial restriction. Do not silently update to upstream main.

## Acceptance facts

- Six separate Runtime runs succeeded under
  `.tools/p17-acceptance/runtime-20260806/`.
- RTLCraft emitted real `PlatformAccumulator.sv`; TCADCraft executed upstream
  3D geometry; MoMCraft executed upstream Touchstone I/O; EDACode and CktCraft
  remained honest audit-only levels; ImplCraft generated three Tcl scripts.
- Full TCAD, EM, SPICE/RF, EDACode LLM, and commercial EDA were not run and are
  not claimed.
- The web interface has exactly five English tabs: Overview, Frontend Design,
  Backend Design, Projects & Results, and Self-Evolution.
- Schematic and layout views continue to use Graphviz and registered KLayout
  artifacts. Slides/videos were not fabricated.
- Cross-page presentation data is projected by
  `apps/api/services/platform_service.py`; Runtime/database stores remain the
  authority.
- Full regression: 182 tests. JS, Python, JSON, credential-pattern, whitespace,
  and directory checks passed.

## Recovery entry points

- `docs/P17_EDACRAFT_WEB.md`
- `docs/evidence/P17_EDACRAFT_WEB_ACCEPTANCE.md`
- `integrations/edacraft/source.lock.json`
- `scripts/run_p17_acceptance.py`
- `docs/progress/NEXT_ACTION.md`
