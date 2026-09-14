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
