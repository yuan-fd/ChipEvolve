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
6,195, which is the 6,167 budget plus the 28 lines documented in
`core_runtime_src_openroad_platform_runtime_store.py.md`. Raising it again means
editing that number and saying why here.
