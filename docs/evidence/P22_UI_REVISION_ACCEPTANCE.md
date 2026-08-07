# P22 UI revision acceptance

Date: 2026-08-07

## Scope

This revision implements the user-reviewed product information architecture
without changing Workflow Runtime ownership or starting optional expensive
tools.

- Six English tabs: Overview, Frontend Design, Backend Design, Extensions,
  Projects & Results, and Self-Evolution.
- Overview: formal product name and positioning, six capability statements,
  demo/slides placeholders, connected workflow, and a recommended tutorial.
- Frontend: vertical three-input workflow, eight synthesizable examples,
  gate/port statistics, circuit schematic, RTL/netlist access, RTLScout workflow,
  and BYOK controls.
- Backend: vertical setup, six-stage status, layout/evidence sections, and
  clickable Flow-Agent, TaiWei 3D, ImplCraft, and DPLEvolve entry points.
- Extensions: TaiWei, Flow-Agent, DPLEvolve, and all six EDACraft components
  expose scope, availability, input, workflow, and recorded-result actions.
- Projects: vertical index followed by a full project detail view with circuit
  or layout, metrics, stages, artifacts, hashes, and raw replay record.
- Self-Evolution: prominent knowledge/observations to GP/BO/RL, human decision,
  Campaign/Runtime, and verified feedback visualization.

## Truth and cost boundaries

- Schematics remain generated from Yosys netlists through Graphviz or the
  deterministic netlist overview; no circuit or layout is hand-drawn.
- Large netlists automatically use a bounded port/cell overview so complex
  examples do not block on a full Graphviz layout.
- DPLEvolve remains optional and cannot start automatically from this UI.
- BYOK still obeys the existing secure-transport gate.
- No expensive DPLEvolve run, commercial EDA run, push, or public deployment
  occurred.

## Verification

- `node --check apps/web/assets/app.js`
- `python3 -m py_compile apps/api/app.py apps/api/services/design_service.py apps/api/services/platform_service.py`
- `python3 -m pytest -q`: **190 passed**
- Live HTTP smoke: health, six-tab HTML, and eight-example API catalog succeeded.
- Headless 1440 px visual inspection covered Overview and Frontend hierarchy.
- User-owned untracked `plan/` content remained untouched.
