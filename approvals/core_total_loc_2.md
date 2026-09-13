# Approval: kernel budget 5869 -> 6167

## What is being added

| File | Lines | Why it is kernel and not an app |
| --- | ---: | --- |
| `core/runtime/worker.py` | 215 | A submitted run only advances when something calls ``execute_once``. Without this, the platform is a library with a queue in front of it, not a platform. Supervising attempts is the runtime's own job. |
| `core/runtime/store.py` (growth) | ~40 | ``runnable_runs`` and ``abandoned_cancellations``. The worker polls on every cycle, so a poll that scanned the whole history would get slower for the rest of the platform's life. |
| `core/runtime/runtime.py` (growth) | ~20 | ``execute_once_reporting``. |

## Why ``execute_once_reporting`` had to exist

The worker must know whether *it* did the work, and it cannot work that out:

* comparing run status before and after counts a worker that merely lost the race
  for the lease, because it still observes the run move;
* comparing attempt counts has the same flaw, since the winner's attempt may
  become visible between the two reads.

Both were written, both were wrong, and the concurrency test reported two workers
as having executed one attempt. Only the method that claims the lease knows, so
it says so. The alternative -- a heuristic in the worker -- would have looked
correct and quietly overstated throughput.

## One behaviour change worth recording

A reclaimed lease now settles its run as LOST, not just its attempt. Previously
the run stayed in ``running`` with no attempt that could ever finish it: a run
nobody would ever see fail. The store test was updated to assert the new
behaviour, and the reason is in the docstring.

## What is not being added

No capability, no parser, no vendor name, no algorithm. G1, G2 and G13 remain
zero across the kernel.
