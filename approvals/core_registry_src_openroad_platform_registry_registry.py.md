# Approval: `core/registry/src/openroad_platform_registry/registry.py` 300 -> 433

## What is being added

The split between what a plugin declares and what the platform decides.

| Where | Lines | What |
| --- | ---: | --- |
| `Provenance` | +30 | the plugin-authored record: licence, source, commit, notes |
| `Admission` (reshaped) | +10 | now the platform's decision: review conclusion, approved commit, reviewer |
| `_load_provenance` | +20 | read the plugin's own file; absent means unstated, not an error |
| `_load_admission` | +30 | read the platform's record; absent means unreviewed |
| `_check_reviewed_commit` | +22 | refuse a checkout that is not the revision that was reviewed |
| `from_directory` | +14 | takes an admissions root |
| `catalogue` | +10 | reports the two sources under separate keys |
| docstrings | +25 | why the two files have two owners |

## Why the split is worth 133 lines

The previous shape asked a third party to maintain a document saying *we* had
approved them.  That is not a smaller version of the same idea; it is the idea
backwards.  A plugin is in a position to state where it came from.  Only this
platform can decide whether that is good enough to execute, and the decision has
to live where the platform keeps its decisions.

Two properties follow, and neither was available before:

* **A plugin cannot admit itself.** Nothing in the plugin's directory grants
  execution; a plugin that tries is told so loudly rather than silently ignored.
* **The reviewed revision is the revision that runs.** If the record names a
  commit and the plugin declares a different one, the registry refuses to load
  it and prints both hashes.  Without this, "pinned commit" is a word.

## Why it is in the registry rather than somewhere else

Reading both files and cross-checking them *is* discovery and admission, which
is what this module exists to do.  Moving the provenance reader to a helper
module would add a file without removing a responsibility.
