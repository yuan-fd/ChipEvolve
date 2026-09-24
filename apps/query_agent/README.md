# Query Agent

Read-only evidence navigation over the execution foundation.

- It imports only `openroad_platform_client` and the standard library.
- It never opens a kernel database or scans a host directory.
- It does not parse design databases or diagnose physical design problems.
- It does not execute commands; approved analysis requests remain a later
  capability owned by the execution foundation and an external adapter. The
  analysis submit route only forwards a task after an explicit confirmation;
  execution and isolation remain foundation responsibilities.
- Smoke: `python3 apps/query_agent/smoke.py`

## Routes

| Route | Purpose |
| --- | --- |
| `GET /health` | App and kernel health |
| `GET /analysis/capabilities` | Registered analysis capabilities and admission state |
| `GET /designs` | Group visible runs into design/version summaries |
| `GET /designs/{design_id}` | One design with its visible run history |
| `GET /designs/{design_id}/tree` | Design → revision → run → evidence tree |
| `GET /runs/{run_id}` | One run, its artifacts, metrics and evidence sources |
| `POST /runs/{run_id}/bundle` | Create one local ZIP evidence artifact |
| `GET /artifacts/{run_id}/{artifact_id}/excerpt` | Bounded original-data preview |
| `GET /artifacts/{run_id}/{artifact_id}/download` | Original artifact bytes |
| `POST /query` | Deterministic natural-language or structured query planning and evidence report |
| `POST /analysis/preview` | Show tool, inputs, estimates and outputs before approval |
| `POST /analysis/submit` | Submit a separately tracked analysis run after `confirm=true` |

The natural-language route is deliberately evidence-first. It converts a
question into filters, reads the same structured views used by the other
routes, and returns sources with every reported fact. A future language model
can replace the planner without changing this evidence boundary.

The implementation keeps replaceable seams explicit: `catalog` owns catalogue
views and stable artifact references, `report` owns evidence assembly,
`planner` owns language/structured intent, `analysis` owns approval previews,
`server` owns HTTP, and `__main__` only starts the process.
