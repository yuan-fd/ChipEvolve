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
