# DSE Lab

Design-space exploration: submit a parameter sweep, track it, and compare the
measured evidence.

## Why this app exists

It is the write path.  `evidence_console` reads; this app composes tasks and
submits them, which is what makes it a second, independent process rather than
another view.

## The three boundaries it keeps

- **It owns its own database and nothing else.**  Sweep definitions live here;
  every run, artifact and metric belongs to the kernel and is read back through
  the client (G5).  There is no second copy of the evidence.
- **It does not validate parameters.**  The allowlist, the bounds and the
  cross-parameter rules live in the plugin that consumes them.  A console that
  re-implemented them would be a second source of truth, and the two would
  disagree the first time a bound changed.  An invalid point is submitted, and
  the rejection is reported as a result.
- **It asserts nothing about quality.**  A point that failed, or whose metric
  cites no artifact, is shown as such and is never dropped from the comparison.
  A sweep that omits its failures flatters whichever policy produced fewer.

## Routes

| Route | Purpose |
| --- | --- |
| `GET /health` | the lab's own health, plus the kernel's |
| `GET /sweeps` | sweep definitions |
| `POST /sweeps` | create a sweep and submit every point |
| `GET /sweeps/{id}` | one sweep with its points and their run ids |
| `GET /sweeps/{id}/comparison` | per-point metrics with provenance, and a summary that counts refusals and failures |

## Run it

```
python3 apps/dse_lab/src/openroad_app_dse_lab/__main__.py \
    --port 8820 --kernel-url http://127.0.0.1:8700 --db dse_lab.sqlite
```

Smoke: `python3 apps/dse_lab/smoke.py`
