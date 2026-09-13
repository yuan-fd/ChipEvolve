# Progress

## Where this is going

Rewrite the 100k-line v1 platform into a thin control plane plus independent
apps.  v1 is archived read-only; nothing here migrates file by file.  Target:
~20k lines total, kernel ~6k, kernel contains zero vendor identifiers.

## Decisions already made (do not relitigate)

| Decision | Value |
| --- | --- |
| Strategy | Archive v1, build v2 fresh. Not an incremental migration. |
| Shape | Kernel library + independent app processes + gateway entry. |
| Topology | One monorepo; each app is its own package, process, database. |
| Algorithms | Return to upstream plugins. Local optimizers leave the product path. |
| Research scripts | Move to a separate repository. |
| Baseline | Kernel must be clean before structural work (see gate status). |

## Done

**Guardrails — the actual foundation.** `guardrails/rules.py` implements fifteen
rules; `guardrails/negative/G*/` holds a deliberately violating fixture for
each.  Every rule has two tests: the real tree is clean, and the fixture is
caught.  Calibration: pointed at v1's kernel-equivalent packages, G1 fires
**274 times** across 18 vendor tokens (`a2_orfo_campaign.py` alone: 32).

Self-inflicted bugs the negative fixtures caught immediately:
- G6 returned `None`, so it silently always passed.
- G13 counted a code-generator's template string as a duplicate
  implementation; it now walks the AST.
- The app scaffolder produced a non-compliant app and a smoke that could not
  start. Both fixed and re-proven.

**App scaffolding.** `tools/new_app.py` generates a compliant app: own
`pyproject.toml`, own entry point, own `smoke.py`. The generated app passes all
gates and its smoke really starts a process and calls it over HTTP. This makes
the correct path the cheapest path — a guardrail that only forbids teaches an
agent to tunnel through the nearest existing file.

**Contracts.** `contracts/` is dependency-free. Two contracts do the heavy
architectural work:

- `RuntimeRequirements` — a capability declares what it needs from the kernel
  as data, replacing v1's `if plugin_id in {"orfs-agent", "a2-orfo"}`.
- `ProgressReport` / `decode_progress_line` — the kernel parses a generic
  envelope; stage names stay inside the plugin. Replaces v1's hardcoded
  `(synth|floorplan|place|cts|route|finish)` regex.

Removed during the port: `RepairAction`'s `core_utilization_pct` and
`minimum_die_size_um` were physical-design knowledge inside a generic contract.

54 tests pass: 30 gate, 24 contract.

**Runtime.** `core/runtime/` is the kernel's memory and its safety net.

- `store.py` — runs, stages, attempts, artifacts, metrics, events over SQLite.
  Transition tables are data, not scattered `if`s, so the state machine is
  testable and the API layer cannot invent its own.  Carried over from v1
  because v1 got these right: attempts are leased; a stale lease becomes LOST
  (terminal, auditable, never overwritten); journal mode is DELETE because the
  state root may be a shared filesystem; the artifact hash is **measured from
  disk**, not read from the adapter's claim.
- `guardian.py` — one command under a wall-clock deadline, whole-tree cleanup
  including `setsid()` descendants.  The bounded drain that stops a noisy child
  from starving its own deadline is preserved.

**Test style changed on purpose.** v1 asserted that a bash child had written a
pid file within 250 ms.  On a loaded host bash needed longer, so the *safety*
tests failed for reasons unrelated to the safety code — and a flaky safety test
gets ignored.  The new tests let the child report its own grandchild pid on
stdout, so no assertion races the scheduler.

**87 tests pass on the aarch64 Linux target**, including the 9 that need
`/proc` and are skipped on the development laptop.

## Next

1. `core/adapter.py` — bounded process adapter: request/result envelope, path
   containment, artifact declaration validation.
2. `core/runtime/runtime.py` — orchestration: submit, lease, execute, validate,
   register, evaluate, record. Plus the generic progress observer.
3. `core/registry` — discover manifests under `plugins/*/`, admission gate.
4. `core/evaluator` — the boundary only; domain parsers become a plugin.
5. Port the ~4,200 lines of ORFS integration knowledge, behaviour as tests
   first.
6. Apps, starting with the one that is already a real app.

## v1 knowledge that must be carried across by hand

Not guessed, not paraphrased — read from v1 and re-derived as tests:

| v1 module | Lines | Why it cannot be improvised |
| --- | ---: | --- |
| `parsers/stage_json.py` | 495 | real stage-report structure, units, field meaning |
| `netlist/` (4 files) | 704 | real netlist format details |
| `orfs_runner.py` | 689 | real invocation, environment, exit-code semantics |
| `orfs_generated_design.py` | 430 | real constraints on generated designs |
| `orfs_parameters.py` | 370 | real parameter ranges and coupling |
| `orfs_adapter.py` | 330 | adapter boundary |
| `orfs_plugin.py` | 239 | manifest and task construction |
| `parsers/cell_coords.py` | 238 | layout density grid format |
| `orfs_reference_designs.py` | 235 | reference design list |
| `orfs_config.py` | 220 | configuration mapping |
| `orfs_protected_evaluator.py` | 214 | protected verdict semantics |

## v1 measurements worth keeping

- 102,852 Python lines; tests 20,575, scripts 22,694, library 58,705.
- Library reachability: 42,133 reachable, 9,512 orphaned, 6,162 test-only.
- `packages/analysis/__init__.py` already declares 37 modules (9,652 lines,
  64.6% of the package) as `_LEGACY_MODULES`. The team's own verdict.
- `apps/api/app.py`: 5,993 lines, 111 dispatcher branches, 18 SQLite databases,
  87 commits. It grew 49.6% *after* being labelled LEGACY.

## How to run

```
cd v2
.venv/bin/python -m pytest -q          # 54 tests, ~0.2s
python3 tools/new_app.py <name>        # scaffold a compliant app
```
