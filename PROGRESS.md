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

`plugins/edair/` is a new capability: turning one raw EDA artifact into a
bounded, provenance-bearing index.  `opensta.py` parses OpenSTA's labelled
timing paths, and the parser's contract is that it is an *index*, not a summary:

* it reads only blocks OpenSTA explicitly labels, and never guesses a value into
  a field for a line it did not understand;
* the result says how much it did **not** capture -- `unparsed_blocks` counts
  labelled blocks it could not turn into a row, and `truncated` says the cap cut
  the report short.  A 400-path report read with `max_paths=256` is not a report
  with 256 paths, and a reader who is not told will misread the distribution;
* a block missing its slack line is counted and skipped rather than emitted with
  an invented slack, because a missing measurement and a measured zero look
  identical once a default exists;
* path ids identify the **block's position in the report**, not the row number,
  so a row does not silently renumber when the parser learns to read one more
  block;
* the adapter registers the raw report alongside the index.  An index whose
  source is not kept cannot be checked.

`plugins/edair/netlist.py` reads the *mapped* netlist a synthesis tool emits.
It does not elaborate: a netlist whose semantics depend on parameters, generate
blocks or arithmetic operators is read structurally, and an unexpected shape is
a reason to look at the raw artifact rather than a fact.  It also does not fail
on a construct it does not recognise -- unrecognised text produces no instance,
and the raw netlist stays the source of truth.

Two conventions are recorded rather than inferred, because both are fragile:

* **Positional connections follow the tool's pin order, not a language rule.**
  A `dff` primitive is `(clk, rst, d, q)`, so its output is the *fourth*
  connection; a `buf`/`not` is `(y, a)`; anything else is `(y, a, b, s)`.  Named
  connections are always preferred, and the fallback exists only for pre-mapped
  primitives that have no names.  A positional `dff` with fewer than four
  connections gets **no output** rather than a guessed one.
* **`one_` and `zero_` are constant wires, not signals.**  A synthesis flow ties
  constants through wires with those names, and treating them as signals would
  invent edges to a net carrying no information.  They are rewritten only when
  they are not ports and nothing drives them -- the name is a convention, and a
  design with a port called `one_` means it.

A continuous `assign` becomes a buffer instance so every edge comes from one
kind of object, and its synthesized name is made unique rather than assuming the
netlist does not already contain `__assign_buf_0`.

`plugins/edair/analyzer.py` adds the structural half: reachability, paths, cuts,
combinational depth, logic cones and clock domains.  The **property that explains
most of its numbers is that flip-flops are not edges** -- a register drives its Q
but conducts no combinational path -- so every depth is combinational and a
register boundary is a natural end of a cone.  v1's boolean half (expression
extraction, truth tables, equivalence) is deliberately **not** carried across: it
is a separate capability with its own cost, and leaving it out means an index
that says less rather than one that guesses.

It also fixed a real defect found by reading v1 closely.  `dff_clock()` fell back
to the first input when no clock pin was named -- but a named dff with no clock
has its *data* first in the filtered input list, so a register was reported as
clocked by its own data net, and two such registers compared as different clock
domains because their data differed.  The positional fallback now applies only
when the pins really were positional.

## A flaky safety test, fixed properly

Two guardian tests failed on the build host, and the reason was measurable
rather than mysterious.  At load average 74,
``bash -c "sleep 0.1 & echo PID:$!; wait"`` took **up to 13.9 seconds** to
complete.  A 1-second deadline therefore fires before the child has forked
anything, and the assertion describes a process tree that never existed.

The earlier version of these tests had replaced a racy pid *file* with a pid on
*stdout* and I had called the race fixed.  It was half fixed: the deadline still
had to outlast process startup.  The tests are now handshake-driven -- the child
reports its grandchild's pid and the stop happens the moment that line arrives --
which is deterministic, because the test refuses to assert about a tree until the
child has said the tree exists.  The deadline path reaches the same
``_terminate_tree`` code, and is covered separately by a test that needs only a
silent child and makes no timing assumption.

Measured result: 3 consecutive runs, all 10 guardian tests passing, ~5s each, on
a host at load 51.

A second test in the same file had the same mistake in a quieter form: it used a
10-second deadline while asserting about *output capture*.  On the loaded host
the deadline fired before bash had printed anything, so a capture test failed
looking like a capture bug.  A deadline in a test exists to stop a hang, not to
assert that something is fast; the deadlines there are now generous enough that
they cannot fire, and the reasoning is in the test.

## The second app

`apps/dse_lab/` is the write path: it composes parameter sweeps, submits them,
and compares the measured evidence.  `evidence_console` reads; this submits.
Between them the two apps exercise both directions of the kernel API, each as
its own process with its own database and its own smoke.

Three boundaries it keeps, each a rule of the architecture rather than a
preference:

* **It owns its own database and nothing else.**  Sweep definitions live there;
  every run, artifact and metric belongs to the kernel and is read back through
  the client.  A test asserts that the app's database contains exactly two
  tables (`sweeps`, `points`) and one run id, not a copy of the evidence.
* **It does not validate parameters.**  The allowlist and the bounds live in the
  plugin that consumes them.  A console that re-implemented them would be a
  second source of truth, and the two would disagree the first time a bound
  changed.  An invalid point is submitted, the kernel refuses it, and the
  refusal is reported as a result.
* **It asserts nothing about quality.**  A failed point and a point with
  unsourced metrics are both shown, and neither is dropped.  A sweep that
  silently omits its failures flatters whichever policy produced fewer of them.
  The comparison carries a claim boundary saying so.

`plugins/orfs/reference_designs.py` carries the pinned source-bundle recipes --
six registered designs plus the fixed-clock recipe the paper comparison used.
This is the benchmark identity, so three properties are enforced rather than
assumed:

* **The clock is part of the recipe, not a tunable.**  Two arms that could each
  choose their own period are not being compared on the same design.
* **The fingerprint covers the recipe, every source file's bytes, and the
  toolchain commit.**  A recipe alone would not notice an edited source; the
  sources alone would not notice a different toolchain, and the same sources
  built by two ORFS revisions are two different measurements.
* **The paper recipe is a separate recipe, not a flag.**  It pins a different
  ORFS commit with a 4.5 ns SDC rather than the current 3.6 ns, and accepting
  the current one in its place would make the L1-to-L2 evidence handoff
  ambiguous about which design was actually built.  It also requires a clean
  checkout, because a dirty tree means the sources may not be the ones that were
  measured.

The ASAP7 unit conversion lives in the table as the boundary: the recipe holds
nanoseconds while the source SDC stays byte-identical and keeps its official raw
value.  And the paper anchor's utilization of 20 is exactly the parameter gate's
SKY130HD lower bound -- those two numbers living in different files is how a gate
ends up rejecting the official starting configuration of the study it is meant to
reproduce, so a test asserts them together.

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

**The toolchain snapshot.** `plugins/orfs/toolchain.py` records which tools
produced a measurement, and the adapter writes it into every attempt workspace
before the flow starts -- a run that fails still has to say what it was failing
with.  It is registered as evidence like any other file, so "the same
experiment" has a referent instead of being a phrase.

Three decisions in it are deliberate rather than mechanical:

* The worktree status is **recorded, not enforced**: a dirty checkout is written
  down as dirty.  The reference-design loader does refuse a dirty tree, because
  there the checkout changes which sources were built.  Same fact, different
  question, different answer.
* A missing file still produces a record naming it -- ``sha256: null`` beside a
  path says "this was expected and is not there", where omitting the key says
  nothing at all.
* Environment **names** are recorded, never values: which variables were
  inherited is reproducibility, their contents can be credentials.

The adapter is told the checkout, and the flow's Makefile is at
``<orfs_root>/flow/Makefile``.  There is exactly **one** layout: an earlier
draft of this module also accepted a Makefile at the checkout root, and that
leniency cost more than it bought -- the recorded ``orfs_root`` stopped saying
which layout a run used, and it forced a helper whose only job was to invert the
ambiguity.  Both are gone; a path that is not a checkout is an error naming the
file it looked for.

The environment the flow runs under and the snapshot that records it are now the
**same object**, resolved once.  Before this, the plugin built a toolchain
environment nobody used and let ``make`` inherit whatever the adapter happened to
be started with -- so PATH order, which decides *which* build of a tool the flow
picks up, could differ between the run and the record that described it.  Two
tests prove the composed environment reaches the child and that the adapter's own
environment is not passed through wholesale; mutating the wiring back to
``env=None`` makes both fail.

Reading v1 while porting also found four things this port had dropped or
invented, all corrected here:

* ``validate()`` in v1 checked that the tools are **executable**, not merely
  present.  The port checked only that the file exists, so an unlaunchable tool
  would have surfaced hours later as a stage error instead of a configuration
  error.
* v1 rejected a toolchain name containing whitespace.  The name is written into
  the flow's environment and into the snapshot; it is an identifier.
* The port invented ``OPENROAD_EXE`` / ``YOSYS_EXE`` / ``ORFS_FLOW_HOME`` as
  environment variable names where v1 used ``OPENROAD_BIN`` / ``YOSYS_BIN`` /
  ``ORFS_ROOT``.  That left two resolvers ("an explicit path wins over the
  environment") in one plugin, disagreeing about the names.  Resolution now lives
  only in ``toolchain.py``, with v1's names.
* v1 fell back to ``~/OpenROAD-flow-scripts`` and ``~/bin/openroad`` when a
  variable was unset.  That is deliberately **not** carried over: it substitutes
  a toolchain nobody named, and the snapshot would then attribute a result to a
  profile the operator never chose.  An unset variable is an error naming it.

## Next

1. `plugins/orfs/` remaining knowledge: the three v1 modules deliberately
   deferred (`cell_coords.py`, `orfs_generated_design.py`,
   `orfs_design_options.py`).
2. More applications, one per capability the objective names: RTL Studio,
   Teaching Workbench, Knowledge Service, Extensions Console, Terminal Bench.
3. Move `scripts/` to its own `research-toolchain` repository.

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
- v2 size: 21,410 Python lines including tests, against 102,852 in v1.

## How to run

```
cd v2
.venv/bin/python -m pytest -q          # the whole suite, ~70s
.venv/bin/python -m pytest guardrails -q  # the fifteen gates, ~0.6s
python3 tools/new_app.py <name>        # scaffold a compliant app
```
