# P17 EDACraft extension pack and web delivery

P17 corrects the earlier product-level “IC Craft” label. EDACraft is a
monorepo with six materially different projects, so the platform exposes six
independent plugin identities and one umbrella catalog. The existing
`edacraft-implcraft@1.0.0` adapter is preserved unchanged.

## Layered extension model

| Component | Layer | Current platform capability | Current truth boundary |
|---|---|---|---|
| RTLCraft | frontend | fixed upstream DSL → SystemVerilog smoke | not an LLM generation benchmark |
| EDACode | frontend | provider/agent/VS Code/tool surface audit | no arbitrary shell or file-write exposure |
| TCADCraft | device | fixed upstream 3D geometry execution | not a full device-solver convergence run |
| MoMCraft | interconnect | fixed upstream Touchstone write/read | not a Method-of-Moments solve |
| CktCraft | circuit | v0.2 CLI/build/netlist source admission | not an OP/AC/HB/PSS solver run |
| ImplCraft | backend | preserved dry-run commercial script generation | no commercial binary/license execution |

Every executable request uses `TaskSpec`, Plugin Registry, Workflow Runtime,
an isolated attempt workspace, allowlisted artifact kinds, and SHA-256
registration. The umbrella catalog is presentation metadata and cannot own
process state.

## Web information architecture

The browser has exactly five English tabs:

1. **Overview** — purpose, architecture, live counts, extension ecosystem.
2. **Frontend Design** — Spec sessions, BYOK, RTL import, netlists, and
   Graphviz-generated schematics; RTLCraft/EDACode appear as extensions.
3. **Backend Design** — OpenROAD/ORFS requests, Runtime stage observation,
   KLayout views, artifacts, TaiWei 3D, and ImplCraft modes.
4. **Projects & Results** — unified design/run index and authoritative detail.
5. **Self-Evolution** — public sources, benchmarks, observed samples, BO/GP
   studies, recommendations, and the bounded learning loop.

Slides and videos are not fabricated. Empty databases produce honest empty
states. The UI renders circuit schematics from the synthesized netlist and
layout images only from registered artifacts.

## API organization

`apps/api/app.py` remains the dependency-free HTTP shell. Design ownership is
in `services/design_service.py`; the new `services/platform_service.py` builds
read-only page models from authoritative stores. It neither schedules tasks
nor invents state. EDACraft discovery and smoke submission are available at:

- `GET /api/extensions/edacraft`
- `POST /api/extensions/edacraft/:component/smoke`
- `GET /api/platform`, `/api/platform/results`, `/api/platform/evolution`

The historical `/api/craft/plans` endpoint remains for compatibility with the
P16 backend-neutral FlowPlan and preserved ImplCraft path.
