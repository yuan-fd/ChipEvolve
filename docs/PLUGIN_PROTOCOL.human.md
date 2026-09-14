# AgenticEDA Plugin Protocol v1alpha1

This document specifies how a capability is attached to an AgenticEDA platform:
what a plugin must provide, what the platform does with it, and what the platform
guarantees in return. It is the wire contract, not a library. A plugin written in
any language, running in any environment, satisfies it by reading and writing
files and by exiting with a code.

Status: **v1alpha1**. The behaviour described here is implemented and exercised by
the platform's own test suite, including against a real toolchain. The version
suffix is honest rather than decorative: it has not yet been frozen by an
implementation written by someone outside this repository. Field names and rules
may change, and when they do the version changes with them.

---

## 1. What a plugin is

A plugin is a **capability**: a program that performs one kind of work and reports
what it produced. It is not a class, not a package, and not an extension of the
platform's code. The platform starts it as a separate process and communicates
with it through files.

Three consequences follow, and they are the reasons the protocol is shaped this
way:

- **A plugin keeps its own environment.** Its dependencies, interpreter, and
  version pinning are its own business. The platform does not install them, does
  not import them, and does not put its own code on the plugin's import path.
- **A plugin keeps its own release cycle.** It is identified by name and version;
  a new version is a new manifest, and the platform can hold several.
- **A plugin is not trusted.** Everything it reports is a claim. The platform
  measures hashes from disk, checks that declared files exist and stay inside the
  attempt workspace, and requires an explicit review record before the plugin may
  run at all.

A plugin never imports an AgenticEDA package. If a plugin needs a helper that the
platform also has, it carries its own copy: the platform's code is not a library
for plugins, and the boundary is easier to keep when it is not.

---

## 2. What a plugin provides

A plugin is a directory. Nothing else about its internal layout is specified.

```
my-capability/
├── my-capability.plugin.json     required: the declaration
├── provenance.json               optional: where this came from
├── adapter.py                    the entry point, or any executable
└── ...                           whatever else it needs
```

The platform finds plugins by scanning one or more directories and taking each
subdirectory as a candidate. A subdirectory with no `*.plugin.json` is skipped
silently: it may be a half-written checkout, and inventing a declaration for it
would be worse than ignoring it.

### 2.1 The manifest

`<name>.plugin.json` is the plugin's declaration: who it is, what it can do, how
to start it, and what it may produce.

```json
{
  "schema_version": 3,
  "plugin_id": "my-capability",
  "plugin_version": "1.0.0",
  "adapter_entry": ["python3", "./adapter.py"],
  "capabilities": ["eda.rtl_to_gds"],
  "supported_arch": ["aarch64", "x86_64"],
  "default_timeout_seconds": 3600,
  "artifact_rules": [
    {"kind": "report", "required": true},
    {"kind": "log", "required": false}
  ],
  "progress_marker": "[progress]",
  "environment": {}
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `schema_version` | yes | Manifest schema version. Currently `3`. A payload from another version is refused, not migrated. |
| `plugin_id` | yes | Stable identity. Must match `[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}`. |
| `plugin_version` | yes | The plugin's own version. The platform can hold several at once and requires the caller to disambiguate. |
| `adapter_entry` | yes | The command that runs the plugin, as an argument list. |
| `capabilities` | yes | Capability names this plugin provides, e.g. `eda.rtl_to_gds`. Non-empty. |
| `supported_arch` | yes | Architectures it runs on, e.g. `aarch64`. A mismatched host is refused before anything is launched. |
| `default_timeout_seconds` | no | The plugin's own ceiling on one attempt. Default `3600`. The effective deadline is the smaller of this and the task's. |
| `artifact_rules` | no | The artifact kinds the plugin may declare, with `required` per kind. Declaring a kind outside this list is a protocol error. |
| `progress_marker` | no | Line prefix for progress envelopes. Default `[progress]`. |
| `environment` | no | Extra environment variables the plugin needs. Values only; they are passed through to the process. |

Unknown fields are **refused**, not ignored. A manifest from a newer schema fails
loudly, because silently dropping a field a plugin depends on turns a version
mismatch into a wrong result.

**`adapter_entry` and paths.** The list is executed as given. Two forms are
recognised:

- An entry beginning with `./` resolves relative to the plugin's own directory
  and may not resolve outside it. `./../../platform/core/adapter.py` is refused:
  a plugin pointing back into the platform is the failure this rule exists for.
- Any other entry is used verbatim. This is how a plugin names **its own**
  interpreter — `/opt/team-x/venv/bin/python`, a compiled binary, a shell script,
  `uv run ...`. The platform does not substitute its own interpreter.

### 2.2 Provenance (optional)

`provenance.json` states what the plugin knows about its own origin. It is
informational: it grants nothing.

```json
{
  "license": "Apache-2.0",
  "source_url": "https://github.com/example/my-capability",
  "source_commit": "e7a0725758c0abde2b69e2cdba783435238748ef",
  "notes": "vendors nothing; the tool is invoked from the path the task names"
}
```

All fields are optional. A plugin that declares nothing is *unstated*, which is
recorded as unstated rather than guessed at.

`source_commit` participates in the review check described in §5.3.

### 2.3 Admission is not the plugin's file

Whether this platform trusts a plugin is **the platform's record**, kept outside
the plugin's directory:

```
<admissions_root>/<plugin_id>.json
```

```json
{
  "plugin_id": "my-capability",
  "status": "admitted",
  "license_review": "green",
  "approved_commit": "e7a0725758c0abde2b69e2cdba783435238748ef",
  "reviewer": "platform-owner",
  "reason": "Apache-2.0, reviewed at the pinned revision"
}
```

A plugin cannot admit itself, and nothing in its directory can grant execution.
Until a record exists, the plugin is listed in the catalogue as unreviewed and
refuses to run. This is the intended state for a new capability: visible,
inspectable, and not executing.

---

## 3. How the platform runs a plugin

One run of a plugin is called an **attempt**. The platform performs these steps
in order.

**3.1 A workspace is created.** A fresh directory per attempt. The plugin's
working directory is this workspace, and every artifact path it reports is
interpreted relative to it.

**3.2 A request file is written.** `adapter_request.json`, atomically, before the
process starts, so a plugin can never read a half-written request.

**3.3 The process is launched.**

```
<adapter_entry...> --request <workspace>/adapter_request.json \
                   --result  <workspace>/adapter_result.json
```

Its working directory is the workspace. Two arguments are always appended, in
this order; the plugin may ignore anything it does not need but must accept them.

**3.4 The environment is composed.** The process receives:

- a short host allowlist: `HOME`, `LANG`, `LC_ALL`, `TZ`, `TMPDIR`, `TEMP`, `TMP`
- everything in the manifest's `environment`
- `OPENROAD_PLATFORM_PLUGIN_ID` and `OPENROAD_PLATFORM_PLUGIN_VERSION`

Nothing else is inherited. In particular the platform does not add itself to
`PATH` or to the plugin's import path, so a plugin that names its own interpreter
gets exactly the environment it declared.

**3.5 Output is captured.** Everything the process writes to stdout and stderr is
appended to `adapter.log` in the workspace and is available to the platform as it
arrives. Lines matching the progress marker are additionally interpreted (§4.3).

**3.6 The deadline is enforced.** The attempt is given
`min(task.timeout_seconds, manifest.default_timeout_seconds)`. On expiry the
process **and its whole descendant tree** are terminated. A plugin that starts
child processes does not need to clean them up; it does need to not be surprised
by the termination.

**3.7 The result is read, if the attempt was allowed to finish.** A process that
was cancelled or timed out has no opinion worth reading: the platform records
`cancelled` or `timed_out` and ignores whatever the plugin wrote.

**3.8 Claims are checked against reality (§5).** Only then does anything reach
durable storage.

---

## 4. What a plugin writes

### 4.1 The request

```json
{
  "protocol_version": 1,
  "plugin": {"plugin_id": "my-capability", "plugin_version": "1.0.0"},
  "task": {
    "schema_version": 3,
    "task_id": "task-1",
    "project_id": "project-a",
    "design_id": "my-design",
    "plugin_id": "my-capability",
    "inputs": { "...": "whatever this capability needs" },
    "parameters": { "...": "its tuning knobs" },
    "timeout_seconds": 3600,
    "max_attempts": 1,
    "expected_artifacts": ["report"],
    "labels": {}
  }
}
```

`protocol_version` versions **this document**. `task.schema_version` versions the
task payload. They are separate fields with separate meanings, and both are
present so a plugin can tell which question it is being asked.

`inputs` and `parameters` are the plugin's own domain. The platform carries them
and does not interpret them, and it does not validate their shape: only the
plugin knows what they should contain. A plugin that receives input it cannot use
must fail with a `configuration_error` (§4.4), not guess.

`max_attempts` is the attempt budget. A failure the plugin marks `retryable` will
be retried until the budget is spent (§4.4).

### 4.2 The result

`adapter_result.json`, written before the process exits:

```json
{
  "schema_version": 3,
  "status": "succeeded",
  "exit_code": 0,
  "started_at": "2026-01-01T00:00:00+00:00",
  "ended_at": "2026-01-01T00:01:30+00:00",
  "artifacts": [
    {"kind": "report", "path": "reports/summary.json", "required": true}
  ],
  "metrics": [
    {"name": "instance_count", "value": 4200, "unit": "count"}
  ],
  "failure": null,
  "provenance": {}
}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `schema_version` | yes | Payload schema version. Currently `3`. |
| `status` | yes | One of `succeeded`, `failed`, `cancelled`, `timed_out`. Any other value is refused. |
| `exit_code` | yes | Must equal the process's actual exit code. |
| `started_at`, `ended_at` | yes | ISO-8601 timestamps, non-empty. |
| `artifacts` | no | Declared outputs (§4.3). |
| `metrics` | no | Measured numbers (§4.5). |
| `failure` | no | Present when the attempt failed (§4.4). |
| `provenance` | no | Free-form mapping. The platform stores it; it does not interpret it. |

Rules the platform enforces:

- `status: "succeeded"` requires `exit_code: 0`.
- The declared `exit_code` must equal the process's exit code. A plugin that
  writes a result while the process exits differently is reporting on something
  other than what happened.
- A missing `adapter_result.json` after a process that was not cancelled or timed
  out is a protocol error.

### 4.3 Artifacts

An artifact is a file the plugin produced, named relative to the workspace.

```json
{"kind": "report", "path": "reports/summary.json",
 "required": true, "media_type": "application/json", "metadata": {}}
```

- `path` must be **relative**, must not begin with `/`, and must not contain
  `..`. The platform refuses anything that resolves outside the attempt
  workspace.
- `kind` must appear in the manifest's `artifact_rules`. Declaring an unlisted
  kind is a protocol error.
- When the result claims `succeeded`, every kind marked `required: true` in the
  manifest must actually be declared by the plugin and must exist on disk with
  non-zero size. An empty file is treated as an absent one: a run that creates a
  zero-byte report has not reported anything. A result that reports `failed` is
  not held to this — a run that failed has no obligation to have produced its
  outputs, and partial evidence is still evidence.
- Two artifact kinds are reserved by the platform — `runtime_protocol_receipt`
  and `protected_evaluation`. A plugin declaring either is refused.
- A plugin may not set the `runtime_authority` or `official_qor` metadata keys.
  Metrics from the platform's own protected evaluator are marked by the platform,
  and an adapter claiming that authority is refused.

The platform hashes every artifact **from the bytes on disk** after the process
exits. A hash a plugin declares is a claim; the measurement is the platform's.

### 4.4 Failure

```json
"failure": {
  "category": "configuration_error",
  "message": "no Makefile found at /opt/orfs/flow/Makefile",
  "retryable": false,
  "detail": {}
}
```

- `category` is the plugin's own short label: `configuration_error`,
  `flow_error`, `tool_error`, `transient_error`, and so on. It is recorded and
  can be filtered on, which is why it should be a stable word and not a sentence.
- `message` is required and must be non-empty. It is what an operator reads.
- `retryable` is **the plugin's decision**, and it is the only party that can make
  it: a missing tool will not appear, a transient read might succeed.
  - `retryable: true` asks for another attempt. The platform grants it while
    `max_attempts` allows, then fails the run.
  - Omitted or `false` means no retry.
  - The platform never retries on its own: a timeout, a cancellation, a lost
    lease and a protocol error are all final. A retry the plugin did not ask for
    is not a kindness.

A plugin that reports `failed` without a `failure` block is still recorded as
failed; it has simply declined to say why, and that absence is visible.

### 4.5 Metrics

```json
{"name": "setup_wns_ns", "value": 7.82, "unit": "ns",
 "source_artifact_id": null, "parser_id": "my-parser",
 "parser_version": "1", "context": {}}
```

- `value` must be a JSON scalar — a number, string, or boolean. `NaN` is refused.
- A metric with no `source_artifact_id` is stored as **unsourced**. The platform
  keeps it and labels it; it does not hide it and does not present it as
  evidence. An unsourced metric is a display value.
- `name` and `unit` must be identifiers, so that a later query can group by them.

Metrics are only registered for attempts that succeeded.

### 4.6 Progress and events

A plugin reports stage progress by writing one JSON object per line to stdout,
prefixed with the manifest's `progress_marker` (default `[progress]`):

```
[progress] {"stage": "synthesize", "phase": "started"}
[progress] {"stage": "synthesize", "phase": "finished", "status": "succeeded", "seconds": 7.9}
```

| Field | Meaning |
| --- | --- |
| `stage` | Any stable label the plugin chooses. The platform treats it as opaque data; it does not know or check any stage vocabulary. |
| `phase` | `started` or `finished`. |
| `status` | With `finished`: `succeeded`, `failed`, or `cancelled`. |
| `seconds` | Optional duration, if the plugin measured one. |
| `detail` | Optional string. |

Rules and behaviour:

- The payload is capped at 4096 bytes. An oversized or malformed envelope is
  counted, not fatal: the run continues, and the count appears in the run's
  events so a plugin that cannot be read does not look healthy.
- The platform de-duplicates: the same `started` line printed twice — as happens
  on a retry — produces one event.
- A `finished` line for a stage that never started is reported as `finished`.
  The platform does not enforce an ordering it cannot know.
- Progress is informational. It never decides the outcome, and writing none of it
  is valid: a run that reports no stages is recorded as having reported no
  stages, which is different from having reported failure.

---

## 5. What the platform will not take on trust

Five checks stand between what a plugin says and what the platform stores. They
are listed here because a plugin author should not have to discover them by
failing.

**5.1 The process's own outcome.** A cancelled or timed-out process: the
platform's verdict, not the plugin's. A missing result file: protocol error.

**5.2 Exit-code agreement.** The declared `exit_code` must equal the process's
exit code, and a claimed success with a non-zero exit is a protocol error.

**5.3 The reviewed revision.** If the platform's admission record names an
`approved_commit` and the plugin's `provenance.json` declares a different
`source_commit`, the plugin does not load, and the error names both hashes. A
team that moves its source needs a new review.

**5.4 Artifact reality.** Paths inside the workspace only; kinds declared in the
manifest only; hashes measured from disk; and, for a result claiming success,
required kinds present and non-empty.

**5.5 Evaluated results.** Where a task requires a protected evaluation, the
platform runs a separate evaluator plugin afterwards and accepts its verdict only
if every metric cites an artifact that exists in the workspace. Adapters cannot
produce that verdict for themselves — that is what "protected" means here.

---

## 6. Writing a plugin

The smallest correct plugin is a program that reads the request, does its work,
writes a result, and exits with a code that matches. The repository's
`plugins/example/` is a complete working instance of about 130 lines: it reads
`inputs`, emits two progress envelopes, writes one artifact and one metric, and
reports failures in the shape of §4.4.

A sequence that works:

1. Write `my-capability.plugin.json`. Start from a working example; the field set
   is small and every field is in §2.1.
2. Write the adapter. Accept `--request` and `--result`. Read the request, do the
   work in the current directory, write the result.
3. Make failures informative. A `failure` block with a stable `category` and a
   message naming the actual path or value is worth more than a stack trace: it
   is what an operator sees, and it is what the retry decision is based on.
4. Declare only what you produce. The artifact kinds in the manifest are a
   contract; declaring one you might not write turns a successful run into a
   protocol failure.
5. Check your work without the platform. A plugin is testable on its own: write a
   request file, run the adapter, read the result file. No AgenticEDA package is
   involved, and none needs to be installed.

### Common mistakes

| Symptom | Cause |
| --- | --- |
| `adapter produced no adapter_result.json` | The result file was written somewhere other than `--result`, or the process exited before writing it. |
| `unknown PluginManifest fields: ...` | The manifest targets a different schema version, or carries a field the protocol does not define. |
| `adapter entry escapes its own directory` | A `./...` entry resolved outside the plugin directory. |
| `required artifact kinds missing` | A kind marked required in the manifest was not declared, or the file was empty. |
| `result exit_code ... does not match` | The result says one thing and the process did another. |
| Nothing runs, and the catalogue says `unknown` | There is no admission record for the plugin. This is the normal state for a new plugin, and it is the platform owner's decision, not a configuration error on your side. |

---

## 7. Versioning

Three numbers exist, and they answer three different questions:

| Number | Where | Question |
| --- | --- | --- |
| `protocol_version` | request envelope | Which version of this document is in play? |
| `schema_version` | manifest, task, result | Which version of that payload's shape is this? |
| `plugin_version` | manifest | Which release of the plugin is this? |

The payload versions move together and are currently `3`. A payload from another
version is refused rather than migrated: the platform does not guess at a shape it
was not built for, because a silently misread field becomes a wrong number that
nobody can trace.

`protocol_version` is `1`. The current protocol has no negotiation: a request
carries the version the platform speaks, and a plugin that cannot speak it should
fail with a `configuration_error` naming the versions involved. A declared
compatibility range in the manifest is a recognised gap, not a feature.

---

## 8. Summary

| The plugin's side | The platform's side |
| --- | --- |
| Declares itself in a manifest | Discovers it by scanning a directory |
| States its own origin, optionally | Decides admission, on its own records |
| Provides an executable entry point | Launches it as a process in a fresh workspace |
| Reads `adapter_request.json` | Writes it atomically before launch |
| Writes `adapter_result.json` | Measures artifacts against disk and workspace |
| Exits with a matching code | Refuses any disagreement |
| Marks failures `retryable` when they are | Owns the retry budget and bounds it |
| Prints progress envelopes if useful | Records them as events, and tolerates their absence |
| Uses its own environment | Passes only what the manifest asked for |

The one rule that explains most of the rest: **the platform owns the lifecycle,
the plugin owns the meaning.** What a stage is called, what a metric means, what
input is valid, and whether a failure is worth retrying are all the plugin's
answers. Whether a process ran, what it actually produced, and whether that
revision was ever reviewed are the platform's.
