# Plugin admissions

These are the platform's own trust records: one file per plugin, named for the
plugin's `plugin_id`. A plugin executes only if its record here says so.

They are **not** the plugin's files. The plugin's own directory carries
`provenance.json`, which states where the plugin came from and under what
licence — facts the plugin is in a position to know. Whether this platform
admits it is a different question, asked by a different party, and the answer is
kept here.

## The record

```json
{
  "plugin_id": "orfs",
  "status": "admitted",
  "license_review": "green",
  "approved_commit": "e7a0725758c0abde2b69e2cdba783435238748ef",
  "reviewer": "platform-owner",
  "reason": "why this decision was made"
}
```

| Field | Meaning |
| --- | --- |
| `status` | `admitted` may execute; `source-audit-only` and `unknown` may not |
| `license_review` | **the platform's conclusion**, green or yellow, not the plugin's own licence identifier |
| `approved_commit` | the revision the reviewer actually looked at |
| `reviewer` | who made the decision |
| `reason` | why; mandatory for anything not admitted, so a blocked capability is never silently missing |

## Two things this file enforces

**A plugin cannot admit itself.** Nothing in the plugin's directory can grant
execution. Deleting this directory does not break the platform; it makes every
plugin unreviewed, and unreviewed plugins appear in the catalogue and refuse to
run.

**The reviewed revision is the revision that runs.** If `approved_commit` names
one revision and the plugin's `provenance.json` declares another, the registry
refuses to load it and names both hashes. A team that updates their source needs
a new review — which is what "pinned commit" has to mean if it is to mean
anything.

## Not to be confused with `approvals/`

`approvals/` records permission to **grow a file size ceiling**. This directory
records permission to **execute a capability**. Different questions, different
reviewers, separate files.
