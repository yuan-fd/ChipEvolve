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

## 881 -> 982: the inputs an attempt was given

The store already recorded what came *out* of an attempt -- artifacts, their
digests, and the metrics that cite them.  It recorded nothing about what went
*in*.  That gave the platform a one-sided evidence chain: a reader could trace a
number back to the file it was parsed from, and could not trace the file back to
anything.

`runtime_inputs` is keyed by `attempt_id`, like artifacts and metrics, and for
the same reason: an attempt is the unit that actually read bytes.  Recording it
on the run would be a claim about attempts the platform did not make.

Three parts, in the order they matter:

* **The table and `record_inputs`** -- written by the runtime after it has copied
  the bytes, so a row here describes a file that is really in the workspace.
  Nothing else may write it; a caller that could would be able to assert a digest
  for bytes it never read.
* **`list_inputs`, and the projection into `describe_run`** -- because an app may
  not open this database (G5), a fact the read model omits is a fact no
  application can ever show.
* **The forward migration** -- `RUNTIME_SCHEMA_VERSION` 1 -> 2.  The previous
  behaviour was to refuse an older root outright, which turns every upgrade into
  a choice between the platform and its run history.  Older roots are now brought
  forward in place.  Every step so far is additive, so re-running the idempotent
  DDL is the whole migration, and the docstring says plainly that a step which is
  *not* additive must be written out explicitly rather than leaning on that.

`present` is stored as an integer with a CHECK, and `sha256` is nullable only for
an input that is absent.  An absent optional input has no digest on purpose:
digesting the empty string would make two different absences indistinguishable
from the same empty file.

## 982 -> 1191: the objects, and the record of where they are

Three parts, and the middle one is the reason the other two can be trusted.

**The object store.**  `objects_root` sits beside the database, with two hex
characters of fan-out -- a physical-design run produces thousands of reports, and
a single flat directory is a listing nobody wants to wait for.  Publishing is by
rename, because a reader that found a half-written object would find it under a
valid name, which is the worst possible way to learn a copy was interrupted.  A
digest that is already present is not copied again; its size is checked anyway,
because a name that is a hash cannot honestly have two sizes.

**The record.**  `storage` says whether an artifact's bytes are in the object
store or in its attempt workspace, and `artifact_path` follows it.  The column
exists because there have been two answers and only one of them is the current
design: guessing would be guessing about evidence.  This is the same reason
`describe_run` now projects it -- an app may not open this database (G5), so a
fact the read model omits is a fact no application can show.

**The migration.**  The first non-additive step in this store, and it is two:
the new column arrives carrying `workspace` for existing rows, since that is
where their bytes actually are; and `runtime_inputs` is rebuilt, because an
input's bytes may now come from the object store and SQLite cannot relax a
`NOT NULL` in place.  Both check before acting -- DDL commits as it goes, so a
crash between the change and the version update leaves a root labelled 2 with a
column already added, and reopening it must not die on that.  The table shape
lives in one named constant used by both the DDL and the rebuild, because two
copies of a shape eventually disagree and the one that disagreed would be the
migration.

# 2026-09-23: process fencing and worker schema (1397 -> 1477)

The runtime store records process identity, performs conditional lease recovery,
and migrates durable worker presence state. The additions prevent duplicate
execution after a stale lease and provide health data for operators. Ceiling:
1477 lines.

# 2026-09-24: generated local bundle custody (1477 -> 1509)

The store can register generated ZIP bytes as ordinary content-addressed
artifacts. This lets the local run exporter package state and evidence without
creating a second storage path or asking the Query-Agent to open the database.

## Reconciled ceiling

This key's number was tightened to the tree as reconciled in
`approvals/core_total_loc.md` ("the execution API, reconciled"): the branch
raised it to cover input objects and a task-approval API that were dropped, and
the ratchet may only shrink. The reason the remaining growth is authorised is in
that section.

## Reconciled ceiling (second move)

1342 -> 1397: the resume decision, the requeue it performs, the `resumable`
column on `runtime_stage_runs` and its migration.  The reason is in
`approvals/core_total_loc.md` ("a queue that explains itself, and a lost worker
that costs a lease").
