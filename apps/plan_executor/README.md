# Execution Plan Service

Independent application. One process, one database, one UI, one smoke.

- Imports permitted: `openroad_platform_contracts`, `openroad_platform_client` only (G3, G4).
- It must never open a kernel database directly (G5).
- Smoke: `python3 apps/plan_executor/smoke.py`

## Why this app exists

It turns an Agent's ordered task list into normal kernel runs. Steps execute
serially, persist their child `run_id`, stop on failure, and may bind exactly one
artifact from an earlier step into a later step's staged inputs.

```bash
openroad-app-plan_executor --kernel-url http://127.0.0.1:8700 \
  --db /srv/openroad-platform-state/plan_executor.sqlite
```

Routes are `POST /plans`, `GET /plans/{plan_id}` and
`POST /plans/{plan_id}/cancel`. The full request and failure contract is in
[`docs/EXECUTION_PLAN_PROTOCOL.md`](../../docs/EXECUTION_PLAN_PROTOCOL.md).

## What it must not do

It does not execute tools, open kernel databases, hash/copy artifacts, allocate
resources, retry attempts, interpret EDA data, or implement a general DAG. Those
responsibilities remain with the kernel, plugins and Agents respectively.
