# Migration inventory

## Migrated now

| Capability | New owner | Decision |
| --- | --- | --- |
| Native RTL-to-GDS automation | `packages/execution` | Keep and harden |
| Persistent queue and worker | `packages/scheduler` | Replace Flask globals |
| Silent timeout and tree kill | `ProcessGuardian` | Required platform kernel |
| ORFS metric parsing | `packages/analysis` | Keep |
| Rule diagnosis and comparisons | `packages/analysis` | Keep; deterministic |
| Density heatmap data | `packages/analysis` | Keep with approximation labels |
| Netlist parser and graph analysis | `packages/analysis/netlist` | Keep |
| GDS/DEF preview | `packages/visualization` | Keep as optional dependency |
| Circuit summary/schematic SVG | `packages/visualization` | Keep as optional view |
| AI analysis prose | caller-provided analysis client | Keep optional, never authoritative |

## Retained only in the prototype for now

| Capability | Reason |
| --- | --- |
| Natural-language RTL generation | Useful baseline, but lacks functional verification |
| Existing Flask API | Process-local state and EDA threads violate the new boundary |
| Existing HTML application | Product reference; component migration follows the API |
| JSON experience pool | Not a mature retrieval contract and currently overstates success |
| Fixed strategy labels | Timing/power presets lack runtime evidence in the installed ORFS |
| Density sweep wrapper | Rebuild as an OptimizationCampaign after child-run contracts |

## Not copied

Runtime outputs, credentials, `.env`, cache directories, fallback backups, and
third-party repositories are not source migration inputs. Existing evidence
stays under `~/iccad/demo_output` until an explicit artifact import tool exists.

