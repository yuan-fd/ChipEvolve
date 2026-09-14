# Approval: `core/runtime/src/openroad_platform_runtime/store.py` 800 -> 828

## What is being added

The store records, for every attempt, the failure the adapter reported:

```json
{"category": "configuration_error", "message": "...", "retryable": false}
```

It stored that and never read it back.  `Attempt` had no ``failure`` field,
`list_attempts` did not load the column, and `describe_run` did not project it.
So a failed run was visible as ``status: failed`` and nothing else: an operator
could not tell a missing toolchain from a crashed tool without going to the
filesystem and reading the adapter's result file, which is the kernel's job.

The 28 lines are the field, the loader, the projection, and
``_optional_json_object`` -- a decoder that reports a stored failure as absent
rather than taking the read model down when a row is truncated or written by an
older version.  That decoder is the read side of a boundary the write side
already validates.

## Why this is not merely a nicer error message

The protocol *requires* adapters to report why they failed, and the runtime
refuses to store a claimed success that disagrees with the process.  Both halves
exist to make a run's outcome auditable.  Dropping the report on the way out
undid them: the platform insisted on information it then made unreachable.

The application contract depends on it too.  An app may not open the kernel's
database (G5), so if the read model omits a fact, no app can ever show it.

## Found by building an application

`apps/run_console` reports what the kernel holds and refuses to invent the rest.
Writing its test for "a run that reported no progress" exposed the gap: the run
was ``failed``, its timeline was empty, and the reason existed nowhere a caller
could reach.  The console's own test now asserts the reason survives to the
reader, which is what keeps this from regressing.

## What is not being added

No capability, no parser, no vendor name, no algorithm.  G1, G2 and G13 remain
zero across the kernel.

## The ceiling itself

800 -> 828, recorded in `approvals/ceiling.json`.  This is the first approval
under the repaired check: the gate compares the number now, instead of testing
that some file existed.

## 828 -> 881: a retry has to be reachable

`schedule_retry` moves a failed stage **and** its run back to `retry_wait`.

Both, because the worker's claim query requires both to be non-terminal
(`store.py`, `runnable_runs`).  Moving only the run would produce a retry that
is recorded and never claimed: the caller would wait forever for something that
cannot happen.  That is the failure mode this method's docstring names, and a
test asserts the run appears in `runnable_runs` afterwards.

The method reuses `run_transition_allowed` rather than testing the run's status
itself, so the rule for which transitions are legal stays in the one place that
owns it.  The attempt that failed stays `FAILED`: the retry is a new attempt, not
an erasure of the old one.
