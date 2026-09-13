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

**Runtime, complete.** `core/runtime/`: durable store; process guardian;
  bounded adapter protocol; orchestration; the generic progress observer.  A
  capability cannot lie about its own result -- a claimed success with a
  non-zero exit code, or an exit_code that disagrees with the process, becomes
  a protocol error rather than a stored success.

**Registry, real.** `core/registry/`: discovery from `plugins/*/` is the only
  way a capability becomes known, `./` adapter entries are contained to the
  plugin's own directory, and admission is *enforced* -- a plugin with no intake
  evidence is listed and refuses to resolve, by name and with a reason.
  `admitted` needs a green/yellow license **and** a pinned commit.

**Evaluator boundary.** `core/evaluator/`: the kernel owns the boundary, a
  plugin owns the domain.  The evaluator runs through the same adapter protocol
  as anything else and its answer is refused unless it is traceable -- every
  metric must cite an artifact that exists in the evaluated workspace, and the
  evaluator's identity is pinned by manifest hash so two runs are only
  comparable when the same evaluator produced both.

**The path works end to end.** `plugins/example/` is discovered from a
  directory, admitted from its own evidence, executed as a separate process, and
  its artifacts, metrics and stage events become durable evidence.  The kernel
  never learns the word "example-reporter", and a test asserts that.

**Gateway.** `gateway/` authenticates, routes, and derives navigation from a
  list of apps.  No database, no kernel import, no capability name -- a test
  asserts that structurally, because this is the component that reached 5,993
  lines last time.

## ORFS knowledge port -- in progress

`plugins/orfs-evaluator/` holds the first, and most correctness-critical, slice:
the stage-JSON normalizer and the signoff gates, read out of the frozen v1 code
line by line rather than reconstructed.

The trap it exists to avoid: OpenROAD reports timing in the active Liberty/SDC
time unit, and **that unit is not always nanoseconds**.  ASAP7 uses ps;
sky130hd and nangate45 use ns.  A parser that simply renames
`timing__setup__ws` to `setup_wns_ns` is wrong by 1000x on ASAP7 and right on
sky130hd -- the worst kind of wrong, because it looks correct until someone
changes platform.  The unit is read from `run__flow__platform__time_units`,
converted, and an unreadable or conflicting unit is a **gate failure**, never a
guess.

Other behaviour carried across deliberately:
- the six stage prefixes (`1_`..`6_`) and the namespace-stripping table
- candidate-key matching with a suffix fallback, so an ORFS rename degrades
  rather than breaks
- per-metric merge of route and finish, because treating one as a replacement
  loses an explicit route DRC of 0 and reports a clean run as missing data
- utilization reported as either a ratio or a percentage
- fmax derived from the clock period and setup slack, and omitted when the
  denominator is meaningless
- the seven signoff gates, including that a missing metric is null and never zero
- immutable evaluation writes: identical retry idempotent, different retry
  refused

## Next

1. `plugins/orfs/` — the execution adapter, ported from v1's `orfs_runner.py`
   (689 lines of real invocation, environment and exit-code knowledge).
2. `core/provenance` — the cross-run artifact graph.  Events, artifacts and
   metrics already live in the runtime store; this is the read model.
3. `core/identity` — extract auth from v1's API service.
4. Apps, starting with the one that is already a real app.

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
- Gate calibration: G1 fires **1,119 times** on v1's kernel-equivalent packages;
  G13 fires **147 times**. Both are zero in v2.
- v2 size: ~7,500 lines including tests, against 102,852 in v1.

## How to run

```
cd v2
.venv/bin/python -m pytest -q          # 54 tests, ~0.2s
python3 tools/new_app.py <name>        # scaffold a compliant app
```
