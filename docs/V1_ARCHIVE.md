# v1 archive record

The previous implementation is **frozen read-only**. Nothing in this repository
migrates it file by file, and nothing here has written to it.

| Field | Value |
| --- | --- |
| Host | `kunpeng-ARM` (aarch64 / openEuler) |
| Path | `/share/home/yuanwenjie/openroad-platform` |
| Branch | `feat/llm-teaching-platform` |
| Frozen at | `bc173b4` — "feat: replace the MCP web console with a terminal-first workbench" |
| Working tree at freeze | clean |
| Size | 102,852 Python lines across 599 files |

## Why it is archived rather than migrated

An incremental migration carries the defensive structure across with the code:
the compatibility branches, the unreachable paths, the duplicated helpers. Every
file moved brings its history along. Rewriting forces each decision to be made
again, which is the only way the structure actually changes.

v1 remains the reference for **behaviour** — especially the parts that encode
real-world knowledge that cannot be re-derived from first principles. See
`PROGRESS.md` for the list of modules whose behaviour must be carried across by
hand, with tests written first.

## What was measured before freezing

| Finding | Number |
| --- | --- |
| Total Python | 102,852 lines / 599 files |
| Library code (packages + integrations + apps) | 58,705 lines / 250 files |
| Reachable from a real entry point | 42,133 lines |
| Orphaned (nothing imports it) | 9,512 lines |
| Reachable only from tests | 6,162 lines |
| Research/acceptance/report scripts | 22,694 lines (2.6% of them are launchers) |
| `apps/api/app.py` | 5,993 lines, 111 dispatcher branches, 18 SQLite databases, 87 commits |
| `packages/analysis` self-declared legacy | 37 modules, 9,652 lines (64.6% of the package) |

Gate calibration against v1, which is how the rules were shown to be worth
having:

| Rule | Hits on v1 |
| --- | ---: |
| G1 (kernel must not name a plugin or tool) | 1,119 |
| G13 (one implementation per concern) | 143, across 109 files |

Both are zero in this repository.
