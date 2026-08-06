# EDACraft Extension Pack

This directory defines the umbrella integration for the six independent
projects in the pinned `ephonic/EDACraft` monorepo. EDACraft is optional: none
of these plugins is required for the core natural-language → RTL → OpenROAD
flow or the self-evolution loop.

| Plugin | Layer | Platform execution boundary |
|---|---|---|
| `edacraft-rtlcraft` | frontend | real local DSL → SystemVerilog smoke |
| `edacraft-edacode` | frontend | constrained source/API audit; no shell tools exposed |
| `edacraft-tcadcraft` | device | real geometry-code smoke; no full solver claim |
| `edacraft-momcraft` | interconnect | real Touchstone I/O smoke; no EM-solver claim |
| `edacraft-cktcraft` | circuit | fixed-source/solver-surface audit |
| `edacraft-implcraft` | backend | preserved P11 dry-run script generation integration |

All executable requests use `TaskSpec`, Plugin Registry, and Workflow Runtime.
The adapter never owns terminal state. The existing ImplCraft files remain in
`integrations/edacraft_implcraft/` to preserve their evidence and compatibility.

The platform currently freezes commit
`739eee0f3ced8fc3cbb6f01b6cc89414758fd898`. Updating to a newer upstream
commit requires a separate license, behavior, and smoke review.
