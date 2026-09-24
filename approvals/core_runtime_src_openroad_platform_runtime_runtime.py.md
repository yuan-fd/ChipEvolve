# Approval: `core/runtime/src/openroad_platform_runtime/runtime.py` 576 -> 605

## What is being added

`_should_retry`, called after an attempt finishes, plus the branch that uses it.
29 lines, most of them the reason.

## The decision it encodes

**The plugin decides what may be retried; the platform decides only the budget.**

A capability reports `retryable: true` in its own failure report, because it is
the only party that knows whether trying again could help -- a missing tool will
not appear, a transient read might succeed.  The platform owns the budget,
because an unbounded retry loop is the platform's problem to prevent.

Everything else is refused: a timeout, a cancellation and a lost lease are not
failures the plugin asked to repeat, and a protocol error is a plugin bug that a
second run would reproduce.  Each of those has a test.

## Why this is the honest kind of growth

`max_attempts` was a field the contract validated and no code read.  The state
machine and the worker's claim query had both been written for retries already
(`RUNNING -> RETRY_WAIT` was allowed; `retry_wait` was in the runnable set), so
the only thing missing was the decision.  Twenty-nine lines turns a promise into
behaviour; leaving it out would have meant deleting the field instead.

## 605 -> 729: placing the inputs, and measuring what was placed

Three pieces, and the middle one is the reason the other two exist.

**`_check_inputs`** runs at submit and does a `stat`, not a digest.  Its job is to
tell the caller their request is wrong while they are still listening, so
"required input is not a readable file: ..." reaches them as a 400 rather than as
a run that fails a minute later.  It deliberately does not promise the file will
still be there at attempt time -- the platform cannot promise that, and pretending
otherwise would be the sort of guarantee that is discovered to be false exactly
when it matters.

**`_stage_inputs`** copies each declared input into the attempt workspace and
digests it *there*.  Two decisions are worth the lines they cost:

* **Copy, not link.**  A hardlink would let an adapter corrupt the caller's
  original through its own input.  A symlink would let it read outside the
  workspace.  There is no sandbox yet, so neither can be allowed, and the price
  is one copy per attempt.
* **Digest the destination.**  `register_artifacts` hashes what is on disk rather
  than what it was told, and for the same reason: a digest of what was *meant* to
  be copied verifies the intention, not the bytes the adapter will read.

**The input manifest** is written into the workspace and registered as a
platform-owned artifact under the reserved kind `runtime_input_manifest`, exactly
as the protocol receipt already was.  That is not decoration.  It gives the whole
input set one digest, so "the same design" is a single hash rather than a set to
be compared element by element -- and it makes the manifest itself citable
evidence, which is what a data layer above this platform needs in order to hang
its records on something the platform actually measured.

The tamper check is the one the receipt already had, now applied to both files
through one loop instead of two near-identical branches.  An adapter that writes
to the platform's own bookkeeping fails the attempt, loudly.

## 729 -> 785: an input may come from an artifact

`_stage_from_artifact` is the whole addition, and its shape is the point: it
copies the bytes out of the object store and then **checks what landed against
the artifact's own record**.  Not because the object store is suspected, but
because a run that says "this came from artifact X" is a claim, and a claim
nobody checks is decoration.  A digest or size that disagrees fails the attempt
with the reason recorded.

An optional reference whose bytes have gone is recorded as absent rather than
failing, which is the same rule optional host inputs already follow.

`_check_inputs` grew one branch.  An artifact id the platform has never
registered is refused at submission *whether or not the input is required*: the
id is part of the request, and a request naming an id that does not exist is
malformed rather than merely unlucky.  An id it has registered, whose object has
since gone, is a different thing -- an integrity problem, discovered as the
bytes are copied.

`read_artifact_excerpt` lost its workspace arithmetic and now asks the store
where the bytes are.  The search over the run's own view stays, because that is
a membership check: a caller authorised for one run must not read another's
artifact by quoting its id.

## 785 -> 808: refusing a bound this host cannot keep

`_check_resources` runs at submission, beside `_check_inputs`, and refuses a
task whose declared bounds the backend cannot enforce.  The question is put to
the adapter (`supports_limits`) rather than answered here, because a container
backend will answer it differently -- and that is the seam this is for.

A bound accepted and not applied is worse than no bound: the caller believes the
machine is protected.  So the refusal is a 400 while the caller is still
listening, not a run that quietly consumes everything.

## Reconciled ceiling

This key's number was tightened to the tree as reconciled in
`approvals/core_total_loc.md` ("the execution API, reconciled"): the branch
raised it to cover input objects and a task-approval API that were dropped, and
the ratchet may only shrink. The reason the remaining growth is authorised is in
that section.

## Reconciled ceiling (second move)

838 -> 860: the capability's `resumable` answer travels from the manifest to the
stage, a resuming attempt reuses the workspace it already had, and the single
reservation calculation moved onto `RuntimeConfig`.  The reason is in
`approvals/core_total_loc.md`.
# 2026-09-23: effective resource, input identity and root enforcement (865 -> 931)

Runtime enforcement now passes the same effective defaulted resource request
used for admission and can reject host inputs outside configured roots. These
are boundary checks required for shared service deployments. The input identity
digest also freezes the bytes behind each run's design baseline. Ceiling: 931 lines.

# 2026-09-24: preserve CPU-time and process-count limits (931 -> 933)

The effective defaulted resource request now retains the caller's CPU-time and
process-count bounds while supplying defaults only for admission resources.
Without this, those limits disappeared before execution and were accepted as
if they were enforced.

# 2026-09-23: verified artifact byte chunks (860 -> 865)

Five additional lines expose bounded, hash-checked bytes from the existing
artifact excerpt path. EDA design results include binary files that cannot be
recovered from lossy text excerpts. Ceiling: 865 lines.

# 2026-09-24: retain failure execution evidence (933 -> 977)

The runtime now captures platform-owned request, result, log, input-manifest
and protocol-receipt files when an attempt fails. Existing registrations are
deduplicated by store key, and any inability to preserve evidence is attached
to the recorded failure instead of being silently discarded. This gives
operators a verifiable failure scene while keeping domain interpretation out
of the kernel.

The export implementation lives in a separate runtime bundle module, keeping
the lifecycle runtime focused on execution while exposing a single local ZIP
artifact through the gateway.

# 2026-09-24: frozen inputs and local evidence custody (977 -> 1105)

Submission snapshots readable host inputs into the local object store and
records the original path only as provenance. Execution stages that snapshot,
so edits after submission cannot change the experiment. Admission and execution
now use the same effective resource request, while stable evidence custody keeps
bundle export possible after workspace cleanup. These are correctness boundaries
for the single-server deployment; no EDA algorithm or plugin branch was added.

The same boundary now supports manifest-declared collection patterns, so a
Toolkit can identify important files it created without forcing the kernel to
understand their contents.

# 2026-09-24: six-gigabyte default and manual large-memory approval (1105 -> 1112)

Ordinary EDA tasks now reserve a six-gigabyte default memory budget. Requests
above that threshold must carry the explicit `memory_approval=manual` label,
making large-machine consumption visible and deliberate.
