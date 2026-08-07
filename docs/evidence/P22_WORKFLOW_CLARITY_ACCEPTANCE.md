# P22 workflow-clarity acceptance

Date: 2026-08-07

## User-facing structure

- Five primary pages; no standalone Extensions page.
- Frontend and Backend retain the narrow numbered sequence used by the supplied
  reference UI: input, configuration, action, live status, then evidence.
- RTLCraft, EDACode, and ImplCraft remain implemented but are hidden where they
  duplicate the primary digital flow.
- TCADCraft, MoMCraft, and CktCraft are embedded as optional device/circuit
  research branches. TaiWei 3D and DPLEvolve remain Backend branches.
- Stage-aware and Agent-guided search are Backend modes, not a separate
  Flow-Agent product card.
- Overview provides an eight-step end-to-end tutorial with API-key labels and
  an accurate explanation of explicit learning collection.

## State and execution behavior

- Page startup does not auto-select a prior design, physical run, or RTLScout
  run.
- Primary labels use readable ordinal names. Full identifiers remain in the
  authoritative expandable record and integrity evidence.
- A separate Workflow Runtime worker owns execution and emits a heartbeat.
  Health and Backend distinguish ready, running, and offline worker states.
- Six pre-existing queued tasks were cancelled without deleting their records.
- A bounded TCADCraft smoke was observed as queued, running, then succeeded.

## Verification

- `node --check apps/web/assets/app.js`: passed
- Python compile for API, read model, and Runtime worker: passed
- `python3 -m pytest -q`: 198 passed
- tracked-secret scan and `git diff --check`: passed
- user-owned untracked `plan/` content remained untouched and uncommitted
