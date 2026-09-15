# Approval: `core/runtime/src/openroad_platform_runtime/guardian.py` 319 -> 451

## What is being added

The supervisor now enforces the resource bounds a task declares.  Until this
round the only bound on an attempt was its wall-clock deadline, which is enough
to stop a run that hangs and useless against one that simply takes the machine:
a flow that grows to 64 GiB or forks two hundred helpers finishes inside its
deadline and takes every other experiment on the host down with it.

Three parts:

| Part | Lines | What |
| --- | ---: | --- |
| `measure` | ~45 | process count, aggregate CPU seconds and aggregate resident bytes, read from `/proc` in one walk of the tree |
| `_breach` | ~28 | which declared bound is past, named with both the measurement and the request |
| the run loop and `ProcessOutcome.exceeded` | ~20 | check on an interval, terminate the tree, and say why |

The rest is the docstrings recording two decisions that were not obvious.

## Why aggregate, and why not `setrlimit`

The obvious implementation is `setrlimit` in a `preexec_fn`, and it is the wrong
one here.  A per-process rlimit enforces a *different rule* than the one the
caller wrote down: `RLIMIT_CPU` bounds one process where the request says the
tree, and `RLIMIT_AS` bounds virtual address space where the request says
resident memory.  A bound that means something other than what it says is worse
than no bound, because it is believed.

So the meter walks the tree -- which this module already knows how to do, because
it has to, to kill it -- and sums.  `cutime`/`cstime` are added to every live
process's own CPU, because a reaped child's time moves into its parent and would
otherwise vanish from the total exactly when a flow that forks per stage spends
most of it.

## The honest limits, recorded rather than hidden

* **Polling, not a sandbox.**  A spike entirely inside one interval is missed.
  The interval is 0.5 s by default, which is far below the timescale of the
  failure this catches, and far above the cost of not walking `/proc` on every
  wake-up -- a cost this module already has one regression on record for.
* **A host that cannot measure is a host that cannot enforce.**  `run` raises if
  limits are declared and `/proc` is absent, and the runtime asks before it
  accepts the task, so the caller is told at submission.  The alternative --
  accept and ignore -- is the species of false promise this repository keeps
  deleting fields over.

## What is not being added

No capability, no parser, no vendor name, no algorithm.  The meter reads
`/proc/<pid>/stat` and `/proc/<pid>/statm`; it does not know what the process is
doing.  G1, G2 and G13 remain zero across the kernel.

## Reconciled ceiling

This key's number was tightened to the tree as reconciled in
`approvals/core_total_loc.md` ("the execution API, reconciled"): the branch
raised it to cover input objects and a task-approval API that were dropped, and
the ratchet may only shrink. The reason the remaining growth is authorised is in
that section.
