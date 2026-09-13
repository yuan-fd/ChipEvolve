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

`plugins/orfs/` holds the execution slice: the design configuration writer and
the stage runner, both read out of the frozen v1 `orfs_runner.py` / `orfs_config.py`.

Knowledge carried across, each with a test:

- The exact `make` invocation: `DESIGN_CONFIG`, `DESIGN_HOME`, `WORK_HOME`,
  `OPENROAD_EXE`, `YOSYS_EXE`, `NUM_CORES`, with `EQUIVALENCE_CHECK=0` and
  `LEC_CHECK=0` because enabling them would change the runtime a candidate is
  scored on and make runs incomparable.
- Parallelism is bounded 1..64 and an out-of-range value is an **error**, not a
  clamp: silently clamping would make recorded resource usage differ from what
  the operator asked for.
- Per-stage artifact gates.  Synthesis accepts either `1_synth.odb` or
  `1_synth.v`, because older admitted revisions end there and newer ones also
  write the database; requiring the database rejects a valid flow.
- `finish` additionally requires `6_final.def`, `6_final.v` and `6_final.gds`.
- A missing layout triggers the dedicated `gds` target rather than being
  reported as absent.
- The floorplan policy: `CORE_UTILIZATION` normally, or **both** `DIE_AREA` and
  `CORE_AREA` when a minimum die size is requested.  `DIE_AREA` alone disables
  ORFS's utilisation-based sizing and still leaves `initialize_floorplan` with no
  area, so every generated sky130/asap7/gf180 task stopped at floorplan.
- The clock period is written in the platform's Liberty unit: **ASAP7 is ps**, so
  a 10 ns request is written as 10000.  This is the exact counterpart of the
  parser's conversion on the way back out.
- The nangate45 PDN template is applied only to nangate45; it names that
  platform's metal layers, so using it elsewhere would silently produce a wrong
  power network instead of an error.
- Only one place-density policy is written, because ORFS treats
  `PLACE_DENSITY` and `PLACE_DENSITY_LB_ADDON` as alternatives.

`plugins/orfs/parameters.py` adds the tuning allowlist: thirteen named
parameters with their kind, stage, bounds, quantization step and the ORFS script
that consumes each one.

The table's most important property is what is **absent** from it.  The clock
period and the SDC constraints are deliberately not searchable, because a QoR
comparison in which the design target can move is not a comparison: "better
timing" would sometimes mean "an easier design", and no downstream statistic
could recover the meaning.

Other recorded behaviour:

- Per-platform calibrated bounds, narrower than the global ones.  SKY130HD's
  utilization lower bound is 20 rather than 30 because the reviewed upstream
  anchor uses 20; a gate that rejected it would refuse the official starting
  configuration of the study being reproduced.
- `place_density` and `place_density_lb_addon` are *alternative policies*, not
  two knobs, so setting both is refused rather than leaving an inactive
  contradictory value in the evidence.
- `detail_placement_padding` may not exceed `global_placement_padding`.
- The addon dimension has no quantization step: it is continuous upstream, and
  imposing a grid here would silently collapse several distinct proposals into
  the same run.
- `parameter_source_evidence` records whether each parameter's declared consumer
  script exists and actually mentions the variable -- catching "we set it and the
  tool ignored it" without running the flow.

One gap in the frozen implementation was closed: it validated only the tuning
dictionary and then used the explicit arguments unchecked when it was absent, so
an out-of-range utilization could pass straight through the gate.  The effective
set is now what gets validated.

`plugins/orfs/compatibility.py` adds the reviewed upstream backport, and with it
a **defect in v2 that reading v1 exposed**.

The frozen runner never invoked `make` in the operator's ORFS tree.  It copied
the whole flow into the attempt workspace first:

    # Never invoke make in the operator-owned ORFS tree.  Materialize a
    # per-Attempt flow copy before any executable step so all possible
    # Makefile/script writes are contained by Runtime's workspace.

v2 was running `make` against the shared tree.  A run that writes into it changes
the toolchain under every other experiment, and the backport below would have
edited shared source rather than a per-attempt copy.

The backport itself is verified rather than assumed: it applies only when the
file's digest matches the reviewed source, the search string must occur exactly
once, and the result must hash to the reviewed patched digest.  A different ORFS
revision is therefore left alone rather than mangled -- a newer ORFS already
carries the fix, so a mismatch is not an error.  Every change is recorded in
`flow_compatibility.json` with its upstream commit, issue, paired OpenROAD commit
and capability probe, and the receipt is written even when nothing was patched,
so "no patch was needed" is evidence rather than an absence to interpret.

The pinned ORFS/OpenROAD pair predates two coordinated upstream fixes, and the
pinned OpenROAD reports GUI support despite exposing no `gui::show` command, so
the flow's own headless check takes the wrong branch and the final report step
fails.  Backporting is the alternative to moving the pin, which would change
every measurement in the study.

**Cost accepted:** staging copies the flow per attempt. That is the price of
containment, and it is what the frozen implementation paid too.

The adapter is exercised as a **real process** against a stub Makefile, so the
whole chain is testable without a toolchain: configuration written, stages run
in order, each gated on the artifact it should have produced, evidence collected
with the right kinds, exit code agreeing with the reported status.

Reading v1's `run()` corrected a design error that functions alone could not
show.  The layout export happens **inside the finish stage and before its gate**,
because the finish gate requires the layout and the export is a make target of
its own.  Gating first deadlocks: the gate fails, the run stops, and the export
is never attempted.  The test fixture then had the same class of bug -- its stub
Makefile did not `include $(DESIGN_CONFIG)` the way ORFS does, so `PLATFORM` and
`DESIGN_NAME` were empty, every path collapsed to `results///base`, and every
gate failed.

Also carried across: `analysis/flow_error.log` recording which stage failed, and
the four milestones, including `functionally_verified: False` -- the platform
never claims that from a synthesis run.

**Identity.** `core/identity/`: accounts, hashed sessions, resource ownership,
  and per-feature allowances, ported from v1's `AuthStore`.  The parts kept are
  the security-shaped ones: a login for an unknown user still pays the full
  PBKDF2 cost, so response time does not enumerate accounts; digests are compared
  with `hmac.compare_digest`; session tokens are stored only as hashes; and
  ownership cannot be reassigned, so a later caller cannot take over an earlier
  caller's experiment.

**Provenance.** `core/provenance/`: read models and lineage.  Applications may
  not open the kernel database, so this is how evidence reaches a screen.  Every
  metric can name the artifact it was read from, the attempt that produced it and
  the run that owns the attempt.  An unsourced metric is **shown with
  `complete: false` rather than dropped** -- the store contains it, and hiding
  that would be worse than displaying it.

## The kernel is complete

| Package | Lines | Owns |
| --- | ---: | --- |
| `contracts` | 1,348 | the shared language; imports nothing |
| `core/runtime` | 2,239 | attempts, leases, workspaces, adapter protocol, protected boundary invocation |
| `core/registry` | 324 | discovery and enforced admission |
| `core/evaluator` | 291 | the protected boundary; validates and pins a verdict |
| `core/provenance` | 359 | read models and lineage |
| `core/identity` | 427 | users, sessions, ownership, allowances |
| `gateway` | 225 | the integration entry point |

**4,952 lines** against a frozen budget of 4,952 in `baseline.json`, which may
only be lowered.  G1, G2 and G13 are zero across all of it, and `contracts` is
now scanned by them too: a contract that names a tool has stopped being generic.

## The platform is wired end to end

`core/client` is the only door an application may use to reach the kernel, and
the entry point now hosts the kernel's own HTTP surface on the same router that
proxies applications.  One dispatcher for the whole platform, where the previous
one had three in three styles.

**The first app.** `apps/evidence_console/` reads runs, metrics and provenance
through the client.  It opens no database, imports nothing but the client, and
runs as its own process with its own smoke.  Its one interesting behaviour is
honesty about absence: a metric that cites no artifact is reported as
``unsourced`` beside the sourced ones, because the kernel records it and a
reader should not have to check each number to find out.

Contract tests prove the door works: an application registers, submits a task, a
worker runs it, and the application reads the resulting artifacts, metrics,
timeline and graph back -- all through HTTP, never by touching the store.

**The worker.** `core/runtime/worker.py` makes runs progress on their own; a
submitted run no longer waits for somebody to call ``execute_once``.  A cycle
reclaims expired leases, settles cancellations nobody could observe, then claims
and executes.  In that order, so a stage whose lease just expired becomes
available in the same cycle rather than the next one.

The concurrency test forced a real design decision.  A worker that lost the race
for a lease still *observes* the run move, so every check it could make for
itself -- status changed, attempt count grew -- reported work it had not done;
two workers each claimed to have executed one attempt.  Only the method that
claims the lease knows, so ``execute_once_reporting`` returns that fact
explicitly.

Two more corrections, both because the tests were right:

* A reclaimed lease now settles its **run** as LOST, not just its attempt.
  Marking only the attempt left the run in ``running`` with no attempt that could
  ever finish it -- a run nobody would ever see fail.
* The worker's cycle caught only two exception types, so a run naming an unknown
  plugin took the whole cycle down with it.

## Next

1. `plugins/orfs/` remaining knowledge: the admitted-flow compatibility patch and
   the toolchain snapshot, both of which have exact byte-level provenance.
2. More applications, one per capability the objective names.

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
