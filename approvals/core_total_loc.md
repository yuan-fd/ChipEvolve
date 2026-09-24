# Approval: kernel budget 4952 -> 5869

## What is being added

| Package | Lines | Why it is kernel and not an app |
| --- | ---: | --- |
| `core/client` | 244 | The only door an application may use to reach the kernel. G5 forbids applications opening the kernel's database and G4 forbids them importing kernel internals, so without a client there is no legal path from an app to evidence at all. |
| `gateway/router.py` | 247 | One HTTP dispatcher for the whole platform. The previous platform had three, in three styles, and the one that grew to 5,993 lines was the one nobody had factored out. |
| `gateway/kernel_api.py` | 263 | The kernel's HTTP surface. Contains no policy: every handler translates a request and calls one kernel object. |
| `gateway/bootstrap.py` | 95 | The composition root -- the one place that names concrete kernel classes. |
| `gateway/app.py` (growth) | 204 | Now routes the kernel surface and proxies applications on the shared router. |

## What is not being added

No capability, no parser, no vendor name, no algorithm. G1, G2 and G13 remain
zero across the kernel after this change.

## Why the entry point hosts the kernel surface

The alternative was a second kernel service process that the entry point proxies
to. That is one more process to run, one more failure mode to diagnose, and one
more place for the two to disagree about a field name -- for no isolation
benefit, because both are the same trust domain. The entry point hosting the
kernel keeps one process, and `kernel_api.py` keeps the routing thin.

## Cost accepted

The budget roughly doubles. It is raised once, in writing, for the transport
between the kernel and the applications the objective requires. Any further
growth needs its own approval, and the budget may be lowered at any time.

## Later ceilings under this key

`core_total_loc_2.md` raised the budget to 6,167. Both of those approvals should
have said *how much* they authorised, and neither did, because the gate only
checked that this file existed. The consequence was not theoretical: the kernel
reached 6,195 lines against the 6,167 budget and every gate stayed green.

`approvals/ceiling.json` now holds the number this key authorises -- currently
6,201, which is the 6,167 budget plus the 28 lines documented in
`core_runtime_src_openroad_platform_runtime_store.py.md` and the 6 below.
Raising it again means editing that number and saying why here.

## 6,195 -> 6,201: a refusal reported as a server error

`gateway/kernel_api.py`, `submit_run`. A task naming a capability the registry
does not have raised `RegistryError`, which escaped the handler and reached the
client as **500**. The request is the problem, so the answer is 400, and the
registry's own message -- which names what it could not resolve -- is passed
through. Six lines: the `try`, the `except`, the re-raise, and the reason.

Worth recording that this is what the ratchet is for. The growth is small and
the change is a correctness fix, and it still took an entry here, because the
alternative is a budget that every "but it is only six lines" erodes.

## 6,201 -> 6,303: a false contract replaced by real behaviour

This round removed five fields the platform never honoured and implemented the
one that was worth keeping.  The kernel grew by 102 lines net, and the growth is
itemised rather than summarised:

| Where | Lines | What |
| --- | ---: | --- |
| `core/runtime/store.py` | +53 | `schedule_retry` -- returning a failed stage *and* its run to the queue |
| `core/runtime/runtime.py` | +29 | `_should_retry` -- who decides a retry is allowed, and who pays for it |
| `core/runtime/adapter.py` | +4 | renaming the wire version, and saying why it is not `schema_version` |
| `contracts/` | +15 | the docstrings recording *why* five fields were deleted |
| `core/evaluator/boundary.py` | +1 | using the version constant instead of a literal |

**Why this is growth and not bloat.**  Everything else in the round *shrank*:
`TaskSpec` lost `workflow_id` and `resources`, `PluginManifest` lost
`input_schema`, `output_schema` and `required_tools`.  What grew is the one
change that replaced a promise with behaviour: `max_attempts` now does
something.  A plugin that reports a failure as `retryable` gets another attempt,
within a budget the platform owns.

The 15 lines in `contracts/` are the explanation of the removals.  Trimming that
prose to fit the ceiling would be trimming the reason a future reader does not
re-add the fields -- which is the whole point of deleting them in writing.

**The ceilings this needs are recorded in `approvals/ceiling.json`.**  Three
per-file ceilings rise with it, each with its own note beside this one.

## 6,303 -> 6,463: the plugin's statement and the platform's decision

The admission record moved out of the plugin's directory and into the platform's
own, which is the change the two-file split is made of.  The growth is itemised:

| Where | Lines | What |
| --- | ---: | --- |
| `core/registry/.../registry.py` | +133 | a `Provenance` record, a `Provenance` reader, an admission reader keyed by plugin id, the reviewed-commit cross-check, and the catalogue reporting both sources |
| `gateway/.../bootstrap.py` | +14 | `KernelPaths` carries the admissions root; the composition root passes it |
| `gateway/.../__main__.py` | +2 | `--admissions-root` |
| `core/runtime/.../worker.py` | +4 | the worker discovers with the same records the gateway does |

**Not a coincidence, and worth stating:** this is the second round in a row where
the kernel grew, and both times the growth bought a property rather than a
feature.  The ratchet's job is not to prevent that; it is to make each one a
written decision with a number attached.  Both are.

## 6,463 -> 6,716: one assembly, two entry points, and a checker

| Where | Lines | What |
| --- | ---: | --- |
| `core/registry/.../validate.py` | +247 | the conformance checker, in the package whose rules it applies |
| `gateway/.../worker.py` | +80 | the worker's command line, at the composition root |
| `gateway/.../bootstrap.py` | +24 | `build_kernel_parts` -- the store, registry and runtime, assembled once |
| `core/runtime/.../worker.py` | **-82** | the assembly and its command line, removed |
| `gateway/.../pyproject.toml` | +1 | the worker's console script |

**Most of this round's growth is a relocation, and the relocation fixed a defect.**

`core/runtime/worker.py` used to assemble the whole kernel itself: it imported
the registry and the evaluator inside a function, which is how it hid a
package-level cycle -- the runtime depends on the evaluator, and the evaluator
depends on the runtime.  Its docstring claimed the composition was "deliberately
the same as the kernel's".  It was not the same; it was a copy, and a copy is
free to drift.

Now there is one `build_kernel_parts`, called by both the gateway and the worker
command line, and `core/runtime` depends on the contract and nothing else.  The
packaging test that asks whether declared dependencies match actual imports is
what surfaced it: `core/runtime` imported two packages it did not declare, and
declaring them would have published a cycle.

## 6,716 -> 7,159: an input has an identity, and the platform places it

Until this round `inputs` was a free-form dictionary.  The kernel did not
interpret it, did not copy it, and **did not digest it**.  `design_id` was a
string used for filtering; two runs carrying the same string could have read
different bytes, and nothing in the platform could tell.  Every comparison
between such runs was therefore unfalsifiable -- which is a strange property for
a platform whose entire claim is that a stored number is worth something.

A task may now declare `staged_inputs`.  The platform copies each one into the
attempt workspace, digests it there, and records the result against the attempt.
The growth is itemised:

| Where | Lines | What |
| --- | ---: | --- |
| `contracts/.../input.py` | +162 | `InputFile` (what the caller declares) and `StagedInput` (what the platform measured) |
| `contracts/.../task.py` | +31 | the field, its validation, its round-trip |
| `contracts/.../version.py` | +14 | `validate_relative_path`, extracted so the rule exists once |
| `contracts/.../artifact.py` | +8 | the reserved kind, and using the extracted rule |
| `core/runtime/.../store.py` | +101 | the `runtime_inputs` table, its reader, its projection, and a forward migration |
| `core/runtime/.../runtime.py` | +124 | staging, digesting, the manifest, the tamper check |
| `gateway/.../kernel_api.py` | +5 | a request naming absent inputs is a 400, not a 500 |

**Why this is kernel and not an app or a plugin.**  Who else could do it?  The
caller cannot: a caller that places its own files and reports its own digests is
reporting a claim, and the platform's one useful habit is to measure rather than
believe.  A plugin cannot: the plugin is the untrusted party, and a digest it
supplied would prove only that it can compute SHA-256.  Staging and measuring an
input is the mirror image of registering an artifact, and artifacts are already
the kernel's.

**The property bought, stated so it can be tested.**  Two runs may be compared
by input digest instead of by a label, and a comparison between runs that read
different bytes is now detectable rather than silent.  A digest of the manifest
as a whole covers the entire input set at once, so "the same design" reduces to
one hash.

**A second thing this round fixed, smaller and worth naming.**  The store
refused to open a state root written by an older build -- including one from the
immediately previous commit.  A research platform that loses its run history to a
routine upgrade is not a platform.  `RUNTIME_SCHEMA_VERSION` is now 2 and older
roots are migrated forward in place; a *newer* root is still refused, because
this build cannot know what a later one wrote.  Seventeen of the lines above are
that path and the comment saying which future changes may not lean on it.

**What is not being added.**  No capability, no parser, no vendor name, no
algorithm.  Nothing here knows what a design is.  G1, G2 and G13 remain zero
across the kernel.

**Cost accepted, and where it will be paid back.**  An input is copied once per
attempt, so a retry re-copies it.  That is the honest price of copy-not-link --
a hardlink would let an adapter corrupt the caller's original through its own
input, and a symlink would let it read outside the workspace, and there is no
sandbox to prevent either.  The optimisation belongs to the artifact store
(content addressing, one copy per distinct byte sequence), which is the next
step and will lower this ceiling rather than raise it.

## 7,159 -> 7,453: artifacts get a home, and an input may come from one

The previous round gave an input an identity.  It left two things undone, and
they were the same omission seen from two sides: an artifact's bytes still lived
in the attempt workspace, and an input could still only come from a host path.
So a run's evidence was as durable as a scratch directory, and the only way to
feed one run's output into the next was for a caller to know where the platform
had put it.

The growth is itemised:

| Where | Lines | What |
| --- | ---: | --- |
| `core/runtime/.../store.py` | +209 | the object store, the recorded storage location, the rebuild migration, and the reader that follows the record |
| `core/runtime/.../runtime.py` | +56 | staging an input from an artifact, and checking it as it lands |
| `contracts/.../input.py` | +26 | `artifact_id` as the second place an input's bytes may come from |
| `gateway/.../bootstrap.py` | +3 | the state root says where its objects are |

**What the object store buys, in one sentence each.**

*An artifact is no longer a scratch file.*  It is copied, at registration, into
`<state root>/runtime-objects/<first two hex>/<digest>`, named for the digest the
platform measured.  A test deletes the attempt workspace and reads the artifact
afterwards, which is the property stated without prose.

*Identical bytes cost one copy.*  Two runs that emit the same report share one
object and one digest; the second registration finds it and stops.

*One run can consume another's output without knowing a path.*  An input may
name `artifact_id` instead of `source`, and the bytes are copied out of the
object store into the attempt workspace and verified against the record as they
land.  A reference that does not verify fails the attempt rather than feeding a
plugin the wrong design.

**Why the kernel, again.**  Same answer as last round.  Custody of the bytes is
the platform's because the platform is the party that measured them; a caller
that kept them would be keeping a claim, and the plugin is the untrusted party.
The only new question this round raised was *where*, and the answer is a design
decision, not a domain one.

**The migration is where the care went.**  2 -> 3 is the first step here that is
not additive, and it is two of them: an existing artifact row has its bytes in a
workspace and no object, so the new column arrives carrying `workspace` rather
than a default that would claim otherwise; and `runtime_inputs` had to be
*rebuilt*, because SQLite cannot relax the `NOT NULL` on `source` and an input's
bytes may now come from the object store.  Both steps check before they act,
because DDL commits as it goes and a crash between the change and the version
update must leave a root that still opens.  Four tests cover the reshape, the
rebuilt table, the interruption, and a root from the newer build being refused.

**What is not being added.**  No capability, no parser, no vendor name, no
algorithm.  The object store knows files and digests; it does not know what a
report is.  G1, G2 and G13 remain zero across the kernel.

**Cost accepted, and the debt it pays.**  There is still one copy per attempt on
the way in, and now one on the way out -- but the way out is deduplicated, and
the way in can be deduplicated the same way the moment the object store is used
as the staging source, which is the next step rather than a redesign.

## 7,453 -> 7,755: a bound on what an attempt may consume

Every round so far has added a way for the platform to *do* something.  This one
adds a way for it to *stop* something, and it exists because of a sentence from
the person who owns the machine: a plugin that is not bounded takes the service
with it.

The growth is itemised:

| Where | Lines | What |
| --- | ---: | --- |
| `core/runtime/.../guardian.py` | +132 | the meter, the comparison, and the termination path |
| `contracts/.../resources.py` | +105 | `ResourceRequest`, and the reasons for each of its three fields |
| `core/runtime/.../adapter.py` | +24 | carrying the bounds to the supervisor, and turning a breach into a named failure |
| `core/runtime/.../runtime.py` | +23 | refusing a bound this host cannot measure |
| `gateway/.../kernel_api.py` | +2 | that refusal is a 400 |

**A field returns, and the reason it is not the same field.**  `resources` was
*deleted* from `TaskSpec` two rounds before this one, with a written reason: it
was "declared, enforced nowhere".  It comes back now because the behaviour that
makes it true exists -- and it comes back with a different shape, which is why
the test that used to assert `resources` was an *unknown* field has been replaced
by one asserting that the *old* shape is still refused.  Reading a field whose
meaning has changed is how a caller keeps believing something is honoured.

**Where the bound is declared, and where it is not.**  It is a task field, not a
manifest field.  A manifest is written by the plugin, and an untrusted party
setting its own ceiling is not a ceiling.  The manifest already answers "what
does this capability need from the kernel"; this answers "how much of the machine
may this experiment use", which belongs to whoever owns the machine.

**What is deliberately not claimed.**  This is a bound, not isolation.  It is
measured by polling, so a spike inside one interval is missed; it covers CPU,
resident memory and process count, and not disk, descriptors or network.  Both
limits are written into the protocol document's known-gaps table rather than
left for someone to discover, because the next person to read "resource limits:
done" will otherwise assume a sandbox.

## 7,755 -> 8,183: the execution API, reconciled

A second agent worked directly in the deployment checkout on the server and
reported four completed features.  The work was real, and it was **not in the
repository**: 846 changed lines and nine new files existed in exactly one
working copy, with the ratchet already red (8,434 against an approved 8,397).
It was committed to `wip/execution-api-20260915` for safety and then reconciled
here, which is what this section records.

**Kept, because it is behaviour this platform needed and had promised:**

| What | Where | Why it stays |
| --- | ---: | --- |
| `POST /kernel/runs/{id}/retry` | store + gateway + client | A failed-but-retryable run could only be retried by the automatic path. A human or an agent can now ask, and the request is recorded as an event |
| Resource reservations | `store.py` | A task that declares resources now **reserves** them; an attempt that cannot fit stays queued instead of starting. This is the half of "do not take the machine from everyone else" that metering alone never provided |
| Actual usage per attempt | `guardian.py` + `store.py` | `cpu_seconds`, `peak_memory_bytes`, `peak_processes`, persisted. Requested-versus-used is now answerable, which it was not |
| `GET /runs/{id}/resources`, `/logs` | new query modules | Read models that keep the HTTP layer off the store's tables |

**Dropped, with the reason:**

* **`InputObjects` and `/kernel/inputs`** — a *third* way to name the bytes a
  task consumes, beside `source` and `artifact_id`. Three answers to one
  question is how a plugin author learns to guess. The agreed replacement is an
  upload area, and it is a design, not a patch on top of three existing paths.
* **`POST /kernel/approvals` and the two CPU/memory task ceilings** — the
  platform deciding *who may run what* is a business rule, not an execution
  fact, and it was not asked for. The capacity check against the machine's own
  60% budget stays; that one is about not taking the machine from everyone else.
* **`TaskSpec.extensions`** — a third free-form bag with no consumer. `inputs`
  and `parameters` already pass an agent's or a plugin's own data through
  untouched; a fourth place to put it is not extensibility, it is ambiguity.
  When a real need appears it is one field to add back, with a written reason.

**Also fixed while reconciling:** the branch had bumped `RUNTIME_SCHEMA_VERSION`
to 7 with three of its four steps empty (`executescript(_DDL)` and nothing
else). That is a version number claiming work that never happened. It is now 4,
with one step that says what it does, and the three attempt columns guarded as
the comment above requires.

**Net effect on this ceiling: it goes down.** The branch's approval was 8,397 for
a tree of 8,434 — over its own number. After the reconciliation the kernel is
8,183, and that is what this key authorises. The ratchet moved in the direction
it is allowed to move.

## 8,183 -> 8,329: a queue that explains itself, and a lost worker that costs a lease

Two gaps, both of which a person feels before any test does.

**A run in `queued` said nothing about why.**  Three different situations look
identical from outside -- the machine is full, nothing is running, something is
stuck -- and they need three different responses from whoever is looking.  The
platform knew and did not say, which sends an operator into the database to find
out.  `ResourceQuery.waiting_for` answers in one line, with the numbers: what the
run reserves, and what is left within the budget.

**A lost worker cost the work.**  A five-hour flow whose worker reboots was
marked failed and had to be started again.  Now the attempt is still marked
`LOST` -- it did lose its worker, and that is evidence -- but the run returns to
the queue and the next attempt continues **in the workspace it already had**, so
a flow that resumes by re-running its own makefile finds its finished stages
where it left them.

Both conditions are required, and neither is a guess:

* the capability must declare `requirements.resumable`.  One that cannot resume
  would restart from nothing, and a platform guessing wrong throws away the five
  hours it was trying to save;
* the attempt budget must have room, because **a lost lease is an attempt** --
  the host really did spend that time.  Without this a machine that loses its
  worker every time would loop for ever, which is worse than stopping.

Growth is itemised: `store.py` +55 (the decision, the requeue, the stage column
and its migration), `runtime.py` +22 (the declaration travels from manifest to
stage, and a resuming attempt reuses its workspace), `resource_query.py` +48
(the explanation), `kernel_api.py` +23 (one read model, used by every route that
returns a run), `contracts` +7 (`RuntimeRequirements.resumable`).

**One thing moved rather than grew.**  `RuntimeConfig.reservation_for` is now the
single place that decides what a task reserves, since the runtime reserves on
that basis and the query explains a waiting run on the same basis.  Two copies
would have disagreed, and the disagreement would have sent someone looking for a
shortfall that was not there.
# 2026-09-23: execution safety, ownership and experiment identity (8334 -> 8732)

The implementation adds stale-process fencing, effective resource enforcement,
input-root policy, artifact ownership checks, plan authentication, worker
presence, queue health, binary artifact download, filtered pagination and
immutable experiment input identity. These close the P0 correctness and
shared-service entry gaps identified by the audit. Ceiling: 8732 lines.

# 2026-09-24: preserve all effective resource limits (8732 -> 8734)

`RuntimeConfig.reservation_for` now carries CPU-time and process-count limits
through the same effective request used for admission and execution. Previously
defaulted CPU and memory reservations accidentally discarded those two caller
limits before the process guardian saw them. The two-line change makes the
resource contract truthful without adding a second implementation.

# 2026-09-23: verified artifact byte chunks (+5 lines)

The existing excerpt endpoint now returns bounded base64 bytes, byte count and
completion state. This lets callers reconstruct registered binary EDA artifacts
without reading attempt workspaces or trusting mutable paths. The runtime checks
the registered SHA-256 before serving each chunk. Ceiling: 8334 lines.

# 2026-09-24: preserve failure execution evidence (8734 -> 8778)

Failed attempts now retain the platform-owned request, result, log, input
manifest and protocol receipt files that exist in the workspace. Each is
copied into the content-addressed artifact store and hash-verified, so an
engineer can inspect a failed run without rerunning a long flow. The helper
also records an evidence error in the failure rather than hiding it.

# 2026-09-24: local tree query and one-click evidence bundle (8783 -> 8928)

The foundation now exposes a Design→Revision→Run tree and can package a run's
state, events and hash-verified artifacts into a local ZIP artifact. This is
deliberately a local export, not a remote-object or multi-node storage system;
large bundles continue to use the existing verified artifact chunk interface.
The store gained one byte-registration path so generated bundles receive the
same custody and provenance as Toolkit-produced files.

# 2026-09-24: include staged input bytes in local bundles (8928 -> 8945)

The export now includes the actual files placed into each attempt workspace,
not only their input manifest rows. Each staged input is checked against its
recorded digest before entering the ZIP, so a bundle contains the complete
declared execution scene.

# 2026-09-24: include runtime envelope files in local bundles (8945 -> 8959)

Successful and failed exports now use the same complete layout, including the
adapter request/result, log, input manifest and protocol receipt files when
present. This makes the exported execution envelope independently inspectable.

# 2026-09-24: reserve failure-evidence artifact kinds (8778 -> 8783)

The five platform evidence kinds are now explicitly reserved in the shared
artifact contract. Toolkits cannot claim the same names and make their own
files look like platform-generated failure evidence.

# 2026-09-24: execution custody and worker reliability (8959 -> 9384)

The kernel now keeps submitted host inputs in the object store, preserves
platform evidence outside mutable workspaces, uses a consistent effective
resource budget, records preflight failures as run events, and lets the worker
scan past temporarily unsatisfiable queue entries. This is boundary behaviour
needed for reliable local shared-service operation; no EDA algorithm or
plugin-specific branch was added.

The final increment also adds bounded artifact chunks and local upload chunks,
keeping large EDA files outside one-request memory limits while retaining hash
verification.

The approval is reconciled at 9,451 lines after the final chunk transport
implementation; the same scope and rationale apply.

Three lines add the explicit verification switch used only by bounded chunk
reads; normal artifact reads continue to re-hash the full object.

The reconciled total is 9,456 lines after formatting and the final endpoint
call-site adjustment.
