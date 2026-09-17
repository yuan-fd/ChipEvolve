# Execution Plan Service

Independent application. One process, one database, one UI, one smoke.

- Imports permitted: `openroad_platform_contracts`, `openroad_platform_client` only (G3, G4).
- It must never open a kernel database directly (G5).
- Smoke: `python3 apps/plan_executor/smoke.py`

## Why this app exists

<fill in the single capability this app owns>

## What it must not do

<fill in the neighbouring concerns this app deliberately does not own>
