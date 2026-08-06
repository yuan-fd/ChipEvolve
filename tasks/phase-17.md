# P17 — EDACraft extension pack, five-page web, and delivery cleanup

## Objective

Integrate all six projects in the pinned EDACraft monorepo as independent,
optional Runtime plugins; preserve the established ImplCraft integration;
replace the old project-centric web prototype with the approved five-page
English information architecture; and leave a clean, documented repository.

## Allowlist

- `apps/api/`, `apps/web/`
- `packages/execution/`
- `integrations/edacraft/`, existing `integrations/edacraft_implcraft/`
- P17 tests, replay script, documentation, state, and memory
- disposable Python/pytest caches under project-owned source directories

## Protected data

- user documents under `plan/`
- historical `artifacts/`, `runs/`, `var/`, and failure evidence
- `.external-src/` and `.tools/` source/toolchain content
- shared ORFS/OpenROAD/TaiWei installations

## Truth gates

- exactly five English top-level web tabs
- six separately named EDACraft components; ImplCraft compatibility retained
- Runtime remains the only process/terminal-state authority
- source audits are never represented as solver execution
- full regression, JS/JSON validation, credential scan, repository audit
- no push, deployment, commercial EDA claim, or credential persistence
