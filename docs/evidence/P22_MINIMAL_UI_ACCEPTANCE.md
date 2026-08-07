# P22 modern-minimal UI acceptance

Date: 2026-08-07

## Design direction

The six-page information architecture remains unchanged. This revision replaces
the editorial/art-directed presentation with a compact efficiency-tool visual
system inspired by clear EDA workspaces without copying their component styling.

- Neutral white and light-gray surfaces establish page and module boundaries.
- Blue is the single primary action, selected-navigation, and link color.
- Sans-serif typography and moderate weights replace the oversized serif system.
- Compact 4/8-based spacing reduces scrolling while keeping related controls
  together and separating different tasks.
- Small radii and visible 1 px hairlines define panels. Ordinary content has no
  decorative shadow.
- Buttons, tabs, inputs, status, empty states, tables, and result viewers share
  one consistent component treatment.
- The DPLEvolve long-task dashboard is hidden until DPLEvolve is explicitly
  selected.

## Visual review

Headless browser screenshots at 1440 px reviewed Overview, Frontend, Backend,
Extensions, Projects, and Self-Evolution. A 390 px viewport reviewed Frontend
navigation, heading hierarchy, input tabs, form controls, and primary action.

The resulting pages preserve the existing vertical task flow and real data
bindings while reducing decorative color, oversized headings, large empty
separations, dark showcase blocks, and magazine-style typography.

## Boundaries

No EDA flow, optional smoke, DPLEvolve task, commercial tool, model provider,
credential, push, or public deployment was triggered by this visual revision.
User-owned `plan/` content remains untouched.

## Verification

- `node --check apps/web/assets/app.js`: passed
- `python3 scripts/check_tracked_secrets.py`: passed, no findings
- `python3 -m pytest -q`: **191 passed**
- `git diff --check`: passed
