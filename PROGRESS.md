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
| Research scripts | **Withdrawn.** Do not move them, and do not create a separate repository for them. |
| Deliverable | The platform base. v1, its `scripts/` and its experiment data are out of scope. |
| Baseline | Kernel must be clean before structural work (see gate status). |

### Why the research-scripts decision was withdrawn

The original plan was to move v1's `scripts/` (138 files, 22,980 lines) into a
separate `research-toolchain` repository.  It conflicted with the freeze on v1 --
moving files out *is* modifying the archive -- and resolving that conflict is not
worth doing: v2 contains no `scripts/` directory at all, so the migration was
never about this repository.  Executing it would have touched a large tree for no
benefit and put the delivery environment at risk.

Recorded rather than deleted, so that a later reader does not re-derive the plan
and carry it out.  v1's `scripts/` is **not** a migration candidate, and its
experiment data is **not** evidence this platform needs to preserve.

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

**The bundle gap, found by following the recipes.** Auditing those six recipes
against what the adapter could spend turned up a defect that no unit test could
have shown, because each half was individually correct: `reference_designs.py`
published `rtl_files`/`rtl_root`/`rtl_include_dirs`/`sdc_path`/
`synth_hdl_frontend`/`fast_route_tcl_path`/`design_options`, and the adapter read
only `rtl_path`.  A reference-design task therefore **failed outright** -- the
reviewed designs could not be run at all -- and the snapshot was hashing bundle
entries the flow never used.  Five of the six recipes carry recipe options
(`asap7/ibex` needs `swap_arith_operators` and `openroad_hierarchical`), so an
attempt that dropped them would have elaborated a different design than the one
reviewed, silently.

What was restored, each with a test that fails without it:

* `design_options.py` -- the closed registry of the three ORFS recipe switches.
  Closed because the writer emits ``export <name> = <value>``: an unvalidated
  key would be an arbitrary Make assignment smuggled in through a task bundle.
  An unknown name is refused, and ``"1"`` is refused too -- it would reach the
  flow as ``export X = 1`` and look identical while bypassing the check.
* The rooted **bundle** as the writer's only shape, with relative structure
  preserved, include directories copied header-by-header, the synthesis
  frontend, a verbatim SDC, and the fast-route script.  A source outside the
  bundle root is an error, because staging it would put a file into the attempt
  that the bundle's identity does not cover.
* The adapter normalizes the task's two shapes ("one file" or "a bundle") in one
  place, and **inference reads the whole bundle**: a bundle's top module is often
  declared in one file and instantiated in another, so inferring from the first
  file names the wrong module or none.  ``top`` names the design, ``design`` is
  only the bundle's label -- ORFS elaborates the former.
* Boundary validation on the names that reach Make and Tcl.  A design name
  carrying a newline would not be a bad name, it would be a second statement.

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

## The first real run

Everything above this point was proven against stubs.  The `<yunpeng-ARM>` host
has the real toolchain, so the adapter was run for real: nangate45, a counter,
`target_stage=synth`, through `plugins/orfs/adapter.py` exactly as the platform
invokes it.

It worked -- `1_synth.odb` at 416 KB in 7.9 s of flow time, 10 artifacts
registered with the right kinds, the backport correctly reporting nothing to
apply, and a snapshot naming OpenROAD `26Q1-1961-g63ed2e0fe5`, Yosys 0.63 and
ORFS commit `51ad1231`.  The milestones stayed honest: `synthesizable` true,
`functionally_verified` false, `implementation_valid` false, `gds_complete`
false.

**It also failed first, and that is the point.**  The first run stopped in 19 ms
with ``scripts/variables.mk:23: *** PLATFORM variable not set.``  That message
names neither the real problem nor the real file.  The cause was one line: the
adapter took ``workdir = result_path.parent`` without resolving it, so a relative
``--result`` wrote relative paths into ``config.mk``.  Two consequences, both
invisible to a stub:

* ``make`` resolves a relative ``DESIGN_CONFIG`` against the directory it runs
  in.  It does not report a missing include; it *re-executes* looking for a rule
  to build the missing file, and the failure surfaces later, from
  ``variables.mk``, naming the **operator's** ORFS tree rather than the staged
  one.  With an absolute config the error becomes precise, and shows the second
  consequence.
* ``export VERILOG_FILES = designs/src/...`` was then resolved against the staged
  flow, so synthesis could not find a source that exists one directory up.

No stub could have shown this, because the stub Makefile never reads
``VERILOG_FILES``; it succeeded either way.  The fix is at the boundary --
resolve the request and result paths -- and it is now asserted directly (the
generated ``VERILOG_FILES`` entries must be absolute), with the assertion
mutation-checked.  The run itself is repeatable as an opt-in test:
``tests/plugins/test_orfs_real_toolchain.py``, skipped unless the toolchain is
named, because it copies the flow tree (1.6 GB here) and runs real synthesis.

## The third application, and a gate that had stopped meaning anything

`apps/run_console/` submits a task and follows it. Its one honest behaviour is
that it **does not invent progress**: no percentage, no bar driven by elapsed
time against an expected duration, no stage list known in advance. It reports the
stage events the kernel holds, in the order the plugin announced them, with the
stage name carried through as opaque data; a run that reported nothing is
reported as having reported nothing rather than as 0% complete. A test asserts
the exact field set of the progress payload, so adding a quantity to it has to be
a decision rather than an accident.

Writing its test for "a run that reported no progress" exposed a kernel defect.
The platform requires every adapter to report *why* it failed, stores that, and
never read it back: `Attempt` had no ``failure`` field and ``describe_run`` did
not project one. A failed run was ``status: failed`` and nothing else -- and
since an app may not open the kernel's database (G5), no application could ever
show the reason either. The read model now carries it, which is what
`approvals/core_runtime_src_openroad_platform_runtime_store.py.md` is about.

**The ratchet had a hole.** Being forced through the approval path is what found
it: `approvals/core_total_loc.md` existed, so G8 skipped every later comparison
against the budget. The kernel was at 6,195 lines against a budget of 6,167 and
every gate was green. An approval that only has to *exist* exempts its component
for the rest of the project's life, which is the same as having no ratchet.

An approval now needs two things: a reason in `approvals/<key>.md` and the amount
in `approvals/ceiling.json`. The gate compares the number, so it can fail again
tomorrow. Both directions are tested, including the one it was blind to --
an approval for less than the component actually is -- and an unreadable grant
fails closed rather than passing by accident.

## The two halves were not wired to each other

`synth` proved the execution chain.  Running the full flow and then the protected
evaluator over it found the defect that mattered most, and nothing but a real run
could have found it:

**The evaluator abstained on every run.**  It looked for the implementation under
``orfs/implementation`` -- the nesting the frozen platform produced -- and the v2
adapter writes at the workspace root, because the kernel hands the evaluator the
attempt workspace and the adapter owns its root.  So every evaluation returned
``incomplete: no ORFS implementation directory``, every run was recorded as
unmeasured, and the plugin's own tests passed the whole time: they built the same
nesting the evaluator was looking for.  A fixture that agrees with the code it
tests proves nothing about the system.

**The design identity did not exist.**  The evaluator hashes
``design_input_manifest.json`` when a task carries no bundle fingerprint.  The
adapter declared that file as evidence (it is in ``EVIDENCE_FILES``) and never
wrote it.  The manifest is the design's identity -- ordered sources, the
``sha256`` of every *staged* byte, include directories, synthesis frontend,
recipe options -- and it now has one implementation, in the function that
materializes the design, because that is the only place that knows the staged
copy.  The evaluator's fallback has something to hash, and two runs whose sources
differ no longer compare as the same design.

The evaluator also stopped deriving its workspace from ``--result``: the kernel
*declares* the workspace it is evaluating, so the adapter reads
``task.inputs.workspace`` and refuses a request that omits it, rather than
resolving against whatever directory the caller happened to use.

**What a real run now produces**, on nangate45 for a counter: six stages in 71 s
of flow time, a signoff layout, and an admissible verdict with nine metrics --
``setup_wns_ns`` 7.82 against a 10 ns period, ``power_W`` 1.18e-05,
``drc_errors`` 0 -- each citing a file that exists in the evaluated workspace.
The whole path is repeatable as an opt-in test.

## The picosecond trap, against a real report

The unit conversion is the correctness trap this whole port is organised around:
OpenROAD reports timing in the active Liberty unit, and that unit is **not always
nanoseconds**.  Until now it was proven by tests built from the table.  It has
now been run for real on **asap7**, whose unit is picoseconds:

| | raw report | verdict | period written |
| --- | --- | --- | --- |
| nangate45 | ``finish__timing__setup__ws`` 7.82 | ``setup_wns_ns`` 7.82 | 10 |
| asap7 | ``finish__timing__setup__ws`` 695.997 | ``setup_wns_ns`` 0.696 | 1000 |

A conversion that did not happen would have reported 696 ns of slack for a 1 ns
clock.  The opt-in real test now proves this by construction: it takes the period
per platform and asserts the slack is positive and *below* the period, so on
asap7 the bound is the unit check.  ``OPENROAD_PLATFORM_REAL_PLATFORM=asap7``
runs the same module against the same real toolchain.

One detail worth keeping, because it is the design working rather than a
coincidence: ``6_report.json`` carries no time-unit key at all.  The unit lives
in ``2_1_floorplan.json`` -- ``"run__flow__platform__time_units": "1ps"`` -- a
different file from the timing numbers it applies to, which is exactly why the
parser looks for the declaration across the stage reports instead of assuming it
sits beside the value.

## The false contracts, settled

Five fields were declared to the outside world and honoured by nothing.  Each is
now either implemented or gone; none was left as "we may implement this later".

| Field | Outcome | Why that outcome |
| --- | --- | --- |
| `input_schema`, `output_schema` | **deleted** | The kernel never validated either. Validating them would mean teaching the kernel a schema dialect -- and giving a dependency-free package its first dependency -- to check a plugin's own domain, which the plugin is the only party able to check. It reports a `configuration_error` instead. |
| `workflow_id` | **deleted** | The contract demanded exactly one of it and `plugin_id`, nothing read it, and a task naming only a workflow failed at the store. |
| `resources` | **deleted** | Declared, enforced nowhere. Real limits need cgroups or containers; the field returns when the enforcement does. |
| `required_tools` | **deleted** | The kernel *cannot* check it: a plugin runs in its own environment, so the kernel would be asking about the wrong `PATH`. The plugin checks its own tools. |
| `max_attempts` | **implemented** | Retry is lifecycle, which is the platform's business. See below. |
| the two keys named `schema_version` | **separated** | The request envelope now carries `protocol_version`; payloads keep `schema_version`. Two questions, two names. |

The manifest schema version moved 2 -> 3, because `known_payload` refuses unknown
fields: a v2 manifest carrying `input_schema` now fails loudly instead of being
silently accepted.  Twenty producers (four manifests, four adapters, tests,
smokes) were updated, which is the point -- the version number is what made the
break visible.

**One field was worth keeping.** `max_attempts` is now real: a plugin that
reports a failure as `retryable` gets another attempt, within a budget the
platform owns.  The state machine and the worker's claim query had been written
for retries already -- `RUNNING -> RETRY_WAIT` was allowed and `retry_wait` was
in the runnable set -- so only the decision was missing.  The division is the
platform's own principle: **the plugin decides what may be retried, the platform
decides how many times**, because only the plugin knows whether trying again
could help, and only the platform can bound a loop.  A timeout, a cancellation,
a lost lease and a protocol error are never retried, each with a test.

## The boundary is now enforced, not just observed

`G16`: the kernel must not import an app or a plugin.  It measured **zero** and
was true only by convention -- and no existing rule would have noticed a
regression, because G1 catches a plugin *name* in kernel text, not the kernel
importing the plugin's code.  This is the failure the previous platform made
first: its worker imported the whole application layer to run one command, which
is why deleting a capability there required editing the kernel.

Mutation-checked: injecting `import orfs` into a kernel module makes G16 fail
with `the kernel imports the plugin 'orfs'`; removing it returns the tree to
green.  The claims in the tier-one list below are now guarded rather than merely
measured.

## Two files, two owners: the admission split

The platform used to read its trust records out of the plugin's own directory.
That asked a third party to maintain a document saying *we* had approved them,
which is not a smaller version of the idea but the idea backwards.

Now there are two files with two owners:

| File | Owner | Answers |
| --- | --- | --- |
| `<plugin>/provenance.json` | the plugin | where it came from, under what licence |
| `admissions/<plugin_id>.json` | the platform | whether that is good enough to execute here |

Two properties follow, and neither existed before.  **A plugin cannot admit
itself**: nothing in its directory grants execution, and a plugin that writes a
trust decision into its own provenance file is told so loudly rather than having
the field ignored.  **The reviewed revision is the revision that runs**: if the
record names an `approved_commit` and the plugin declares a different
`source_commit`, the registry refuses to load it and prints both hashes.  Without
that, "pinned commit" is a word.

The new `admissions/` directory is deliberately not to be confused with
`approvals/`: one is permission to execute a capability, the other is permission
to grow a size ceiling.  Its README says so, because two directories named alike
and doing different things is how a reader ends up trusting the wrong one.

The owner's rule for the conformance tool is recorded here as a design
constraint: **self-checking is an aid to acceptance, not a gate.** A plugin
validates itself in its own CI; admission into the platform stays a review the
platform performs.  The tool does not admit anyone.

## The plugin protocol, written down

`docs/PLUGIN_PROTOCOL.human.md` and `docs/PLUGIN_PROTOCOL.agent.md`, both
v1alpha1.  Two audiences, one contract: the first is prose with a worked
walkthrough and a table of what commonly goes wrong; the second is normative, one
rule per line, with the constants and a validation checklist an agent can execute
against its own output.

Both transcribe a protocol that already existed and was already exercised -- the
request and result envelopes, exit-code agreement, artifact containment, the
progress envelopes.  What they add is the part that did not exist: which fields
are required, which are refused, who decides a retry, and what the platform will
not take on trust.  The `agent` file ends with a **known gaps** table so that
absent behaviour cannot be mistaken for specified behaviour.

The alpha suffix is honest.  The protocol has been exercised by this repository's
own plugins and by a real toolchain; it has not yet been frozen by an
implementation written outside it, and the next round is what tests that.

## An external plugin, proved

`edair` was moved out of this repository into its own.  It is discovered by
pointing the platform at that repository, admitted by this platform's own record,
executed as a process in its own environment, and its artifacts, sourced metrics
and progress events are recorded -- with no platform source file changed to
accommodate it.

The platform's test suite lost exactly the plugin's own tests (84 of them) and
nothing else.

**The experiment found the defect it was for.**  `edair` expected its input to be
already present in the attempt workspace, and its tests *wrote the file there
themselves* -- so the plugin looked runnable, while in fact nothing in the
platform ever stages a file.  The fixture supplied what the platform does not.
The plugin now fetches its own input by absolute path and copies it into the
workspace before indexing it, because the platform hashes what is inside the
workspace.  This is the second time a fixture has agreed with the code instead of
with reality; the first was the ORFS reference-design bundle.

It also found a **gap in the protocol document**: an adapter sources a metric by
naming its file in `context.source_artifact_store_key`, and the document did not
describe that mechanism at all.  Both documents now do.  Writing the plugin from
the document is what surfaced it -- had it been written from memory of the
platform's source, the gap would still be there.

## A conformance tool, in the registry

`openroad-platform-plugin-validate <plugin-dir>`, implemented as a module of the
registry package rather than a script under `tools/`.  Two reasons, both learned
by writing it wrong first: as a script it mutated `sys.path` to reach the
contract, which G11 flagged -- correctly -- and it restated the registry's own
rules from outside, which is a second set of rules free to disagree with the
first.

It reports; it does not admit.  And in its first draft it refused a manifest that
listed a platform-reserved artifact kind -- which the protected evaluator *must*
do, or its own verdict would fail its own allowlist.  The tool was stricter than
the platform, and the platform was right.  A validator that rejects what the
platform accepts is a validator that lies.

## Packaging that matches the imports

`contracts` gained the `pyproject.toml` it never had while eight packages
declared it as a dependency, and the three applications now declare the client
they actually import instead of the contracts package they never did.

A new test asks the harder question a `pyproject.toml` cannot answer by existing:
whether every declared dependency is real and every real one is declared.  It
immediately found that `core/runtime` imported the registry and the evaluator
without declaring either -- and the reason they were not declared is that
declaring them would have published a **cycle**: the runtime depends on the
evaluator, and the evaluator depends on the runtime.

The cycle was hidden behind an import inside a function in
`core/runtime/worker.py`, which assembled the whole kernel itself -- a second
composition root, whose docstring claimed to be "deliberately the same as the
kernel's".  It was a copy.  There is now one `build_kernel_parts`, called by both
the gateway and the worker command line, and `core/runtime` depends on the
contract and nothing else.  The worker's command line lives at the composition
root, where knowing concrete implementations is the job.

## The headline external proof

`orfs` and `orfs-evaluator` left: 3,181 lines, a toolchain, a flow and a signoff
evaluator, now in `agenticeda-orfs` beside this repository rather than inside it.
This repository's `plugins/` contains `example` and nothing else -- a reference
capability with no external dependency, which is what lets the platform's own
suite stay meaningful without any real capability present.

**The measurement this was for:** extracting the largest capability the platform
knows required **zero kernel changes**.  Not one line of `contracts`, `core` or
`gateway` was edited to accommodate it, and after the move the kernel is the same
6,716 lines.  That is what "the platform does not grow when a capability is
added" means when it is measured rather than asserted.

What moved and what did not:

| | |
| --- | --- |
| Moved | the two plugin directories and their 14 test files |
| Stayed | `admissions/orfs.json` and `admissions/orfs-evaluator.json`, because a trust decision belongs to the platform |
| Edited on the platform side | `pytest.ini` (three path lines) and one test that had depended on `orfs` being installed |

That last one is worth recording.  `test_run_console.py` submitted an ORFS task
to produce "a failed run with no progress events".  A platform test that depends
on a particular capability being installed is a test that breaks when the
capability leaves, so it now builds its own plugin -- one whose entrypoint does
not exist -- in a plugin root the test owns.  The platform's tests now depend on
no capability at all.

**Running it found a defect in the plugin**, which is why the experiment is worth
more than the assertion.  A `synth`-only run came back **rejected** by the
evaluator (`missing_required_metrics, unverified_time_unit`), and the platform
correctly treats a rejection as a failure -- so every partial run looked like a
rejected design.  The verdict had two states where the domain has three: "no
signoff data was produced" is *incomplete*, "the signoff data fails" is
*rejected*.  Only the plugin can tell them apart, because only it knows what
`finish` means.  Fixed there, with a test.

## G17: the kernel's packages must form a DAG

The cycle found last round was real, was hidden behind an import inside a
function, and was invisible to every other gate here.  `G17` walks
`KERNEL_DIRS`, builds the package import graph from the AST -- including imports
inside functions -- and names the cycle it finds.

Mutation-checked with exactly the hiding technique that concealed the original:
a function-local import in `store.py` produces
`openroad_platform_evaluator -> openroad_platform_runtime ->
openroad_platform_evaluator`, and removing it returns the tree to green.

## Next

**The platform project's remaining work is finished.**  Its completion criteria
were: a new plugin requires no kernel change; the kernel does not grow with the
number of plugins; deleting any plugin leaves the platform working.  All three
now have evidence from two external repositories.

What follows is not platform work:

1. **More applications** -- the teaching workbench, RTL Studio, a knowledge
   service.  These are products, they own their own processes and databases, and
   they reach the platform only through the client.
2. **More capabilities** -- including the research optimizers that left the
   product path in the first decision.  Each is a repository, a manifest, an
   adapter, and an admission review.
3. **Input staging**, if a second plugin is written that has to implement it
   itself.  One is not evidence, and the platform does not grow a semantics
   nobody has needed yet.

Three things will not be proposed without a new, concrete reason: a workflow
engine, a plugin SDK, and any change that brings a capability back into this
repository.

Deliberately not next: more application development, and a workflow engine.  The
orchestration question is a decision to record, not a feature to build -- an
application can already sequence two runs through the client, and the platform
should not grow a semantics it has not been shown to need.

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

### Not ported, and why

Audited rather than assumed.  Each of these has no consumer on v2's product
path, and the condition that would bring it back is written down so a future
reader does not have to re-derive the decision -- or, worse, port it by default.

| v1 module | Lines | Why it is not here |
| --- | ---: | --- |
| `orfs_generated_design.py` | 430 | Exists to bridge ORFS design *discovery* for the upstream AutoTuner, which hard-codes `flow/designs/<pdk>/<design>`. Its only real consumer in v1 is `official_autotuner.py`, and D4 sends optimizers out of the product path. It is also the one module that writes **inside the shared toolchain checkout** -- it installs `flow/designs/<pdk>/opv2_<top>_<hash>/` there -- which is exactly what v2's per-attempt staging exists to prevent, and three such directories are sitting in the operator's tree right now. **Returns if** a plugin that ignores `DESIGN_HOME` is added. |
| `orfs_design_options.py` | 38 | *Was* in this category by association -- it is imported by `generated_design` -- but `orfs_config.py` imports it too, on the product path, and five of the six reference recipes carry options. Ported as `design_options.py`; the bundle that carried them could not spend them. |
| `parsers/cell_coords.py` | 238 | The placement-density grid format. v1 consumers are `pipeline.py`, `diagnosis.py`, `reporter.py` and `apps/api/app.py`; v2 has no diagnosis or layout-visualization capability. **Returns if** an app renders the density grid. |

## v1 measurements worth keeping

- 102,852 Python lines; tests 20,575, scripts 22,694, library 58,705.
- Library reachability: 42,133 reachable, 9,512 orphaned, 6,162 test-only.
- `packages/analysis/__init__.py` already declares 37 modules (9,652 lines,
  64.6% of the package) as `_LEGACY_MODULES`. The team's own verdict.
- `apps/api/app.py`: 5,993 lines, 111 dispatcher branches, 18 SQLite databases,
  87 commits. It grew 49.6% *after* being labelled LEGACY.
- Gate calibration: G1 fires **1,119 times** on v1's kernel-equivalent packages;
  G13 fires **147 times**. Both are zero in v2.
- v2 size: 15606 Python lines including tests, against 102,852 in v1.

## How to run

```
cd v2
.venv/bin/python -m pytest -q          # the whole suite, ~70s
.venv/bin/python -m pytest guardrails -q  # the fifteen gates, ~0.6s
python3 tools/new_app.py <name>        # scaffold a compliant app
```
