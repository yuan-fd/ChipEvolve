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
