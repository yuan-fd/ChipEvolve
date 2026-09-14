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
