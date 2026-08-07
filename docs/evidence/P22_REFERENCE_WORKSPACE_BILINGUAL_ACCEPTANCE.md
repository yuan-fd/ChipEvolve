# P22 reference-workspace and bilingual UI acceptance

Date: 2026-08-07

## Interface result

Frontend and Backend now use the supplied ICCAD interactive-demo screenshots
as their structural reference: a narrow centered workspace, compact top mode
switcher, numbered gray panel headers, vertically ordered operations, controls
next to their effects, and results immediately below the operation that creates
them. Existing platform typography and colors are retained; the reference is
not copied as a static image.

Frontend order:

1. upload RTL or create a natural-language specification session;
2. choose an audited example from visible chips;
3. inspect gate statistics, schematic, RTL, and netlist;
4. optionally open the RTLScout workflow, provider configuration, dashboard,
   and EDACraft frontend alternatives.

Backend order:

1. choose a registered frontend design;
2. configure clock, floorplan parameters, target stage, objective, and mode;
3. submit one baseline Runtime run or create a bounded Campaign plan;
4. monitor the six physical-design stages as vertical rows and a completion bar;
5. inspect layout, registered QoR metrics, artifacts, and optional extensions.

Baseline mode creates one durable Runtime run. Campaign and Agent modes create
three bounded, unbound candidates for review and do not execute them
automatically. Objective and flow-mode provenance are recorded on their task
labels.

## Language behavior

The header contains a persistent `中文 / EN` switch. It changes document
language, primary navigation, the complete frontend/backend task path, key
Overview/Extensions/Projects/Self-Evolution headings, action labels, status
copy, and empty states. Technical names such as RTL, GDS, Runtime, Verilator,
Yosys, and plugin names remain unchanged. `?lang=zh` and `?lang=en` are also
supported for review links.

## Verification

- 1280 px English Frontend and Backend screenshots reviewed
- 1280 px Chinese Frontend screenshot reviewed
- 390 px Chinese Backend screenshot reviewed
- `node --check apps/web/assets/app.js`: passed
- `python3 -m py_compile apps/api/app.py`: passed
- `python3 -m pytest -q`: **196 passed**
- No EDA run, Campaign execution, model call, credential, push, or public
  deployment was triggered by this revision
- User-owned `plan/` content remains untouched and uncommitted
