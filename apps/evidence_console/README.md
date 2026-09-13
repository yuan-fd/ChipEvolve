# Evidence Console

Read-only presentation of the kernel's runs and their provenance.

## Why this app exists

It is the first application built on the v2 architecture, and its job is to
prove the application contract rather than to do anything clever: it reads
evidence through `openroad_platform_client`, holds nothing of its own, and
cannot see more than the caller's token allows.

## What it must not do

- Open a database. It has none, and it must never open the kernel's (G5).
- Import kernel internals. Only the client and the standard library (G4).
- Execute anything. Submission, cancellation and evaluation belong to the
  kernel and the capabilities; this console only shows what already happened.

## Routes

| Route | Purpose |
| --- | --- |
| `GET /health` | the console's own health, plus the kernel's |
| `GET /runs?limit=` | recent runs |
| `GET /runs/{run_id}` | one run, its metrics, its timeline, and an explicit statement of how many metrics cite no artifact |

## Run it

```
python3 apps/evidence_console/src/openroad_app_evidence_console/__main__.py \
    --port 8810 --kernel-url http://127.0.0.1:8700
```

Smoke: `python3 apps/evidence_console/smoke.py`
