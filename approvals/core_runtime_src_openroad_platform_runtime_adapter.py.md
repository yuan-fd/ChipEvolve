# Approval: `core/runtime/src/openroad_platform_runtime/adapter.py` 370 -> 374

## What is being added

Four lines: `PROTOCOL_SCHEMA_VERSION` becomes `PROTOCOL_VERSION`, the request
envelope carries `protocol_version`, and the comment says why.

## Why the rename is not cosmetic

The request envelope and the payloads inside it version independently.  Both
were called `schema_version`, with different values (1 and 2), so a plugin author
saw one key with two meanings and could not tell which question was being asked.
Renaming the wire version makes the two questions distinguishable, and it is
cheap now precisely because no third party has written against either yet.

## 374 -> 398: carrying the bounds, and naming a breach

`limits` travels with the task to the supervisor, which is the only component
that can see a process tree.

The interesting part is what happens when the tree goes past a bound: the
attempt becomes `failed` with `failure.category == "resource_exceeded"`, and the
adapter's result file is **not read**.  A plugin that ignored its bounds does not
get to report on them; the platform decided this outcome, exactly as it does for
a timeout or a cancellation.  It is also not retryable -- the same request
against the same bounds breaches them again -- and the message carries both the
measurement and the request, because "exit code 137" sends an operator looking
for a crash that never happened.
