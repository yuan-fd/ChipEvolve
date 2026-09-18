# AgenticEDA Plugin Protocol v1alpha1 — agent specification

Normative. Every rule is checkable. Field names are exact. Where a rule has a
consequence, the consequence is stated.

Scope: attaching a capability to an AgenticEDA platform as an external process.
Not in scope: any Python API. There is none for plugins. A plugin MUST NOT import
an AgenticEDA package, and the platform MUST NOT import plugin code.

Constants:

```
PROTOCOL_VERSION      = 1
PAYLOAD_SCHEMA_VERSION= 3      # manifest, task, result  (the three move together)
REQUEST_FILENAME      = "adapter_request.json"
RESULT_FILENAME       = "adapter_result.json"
LOG_FILENAME          = "adapter.log"
MANIFEST_SUFFIX       = ".plugin.json"
PROVENANCE_FILENAME   = "provenance.json"
DEFAULT_PROGRESS_MARKER = "[progress]"
MAX_ENVELOPE_BYTES    = 4096
IDENTIFIER            = ^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$
```

---

## A. Plugin directory layout

```
<plugins_root>/<anything>/<anything>.plugin.json     REQUIRED
<plugins_root>/<anything>/provenance.json            optional
<plugins_root>/<anything>/<adapter and its files>    REQUIRED (at least the entry)
```

| ID | Rule |
| --- | --- |
| A1 | The platform discovers plugins by iterating the **direct children** of `plugins_root` and globbing `*` + `MANIFEST_SUFFIX` inside each. |
| A2 | A child directory with no matching manifest is skipped without error. |
| A3 | One child directory MAY contain several manifests (several versions). |
| A4 | `plugins_root` missing entirely is an error, not an empty registry. |
| A5 | Admission records are read from `admissions_root/<plugin_id>.json` — **outside** the plugin directory. The plugin MUST NOT carry its own admission. |

---

## B. Manifest `<name>.plugin.json`

Fields. Unknown fields are a hard error (`unknown PluginManifest fields: ...`).

| Field | Type | Req | Constraint |
| --- | --- | --- | --- |
| `schema_version` | int | Y | MUST equal 3. |
| `plugin_id` | str | Y | MUST match IDENTIFIER. |
| `plugin_version` | str | Y | MUST match IDENTIFIER. |
| `adapter_entry` | list[str] | Y | Non-empty; each element non-empty. |
| `capabilities` | list[str] | Y | Non-empty. Free-form capability names. |
| `supported_arch` | list[str] | Y | Non-empty. Compared against `platform.machine()`. |
| `default_timeout_seconds` | int | N | > 0. Default 3600. |
| `artifact_rules` | list[obj] | N | Each `{"kind": str, "required": bool}`. `kind` MUST match IDENTIFIER. A platform-reserved kind may be listed as an allowlist entry, but an ordinary adapter still may not declare it in its result. |
| `progress_marker` | str | N | Non-empty, ≤ 32 chars. Default `[progress]`. |
| `environment` | obj[str,str] | N | String keys and values only. |

| ID | Rule |
| --- | --- |
| B1 | `plugin_id` MUST be unique per `plugin_version`. Registering the same pair twice is refused. |
| B2 | Two registered versions of one `plugin_id` make `resolve` ambiguous; the caller must pass a version. |
| B3 | `adapter_entry[0]` SHOULD be an absolute path to the plugin's own interpreter or executable. It is executed verbatim. |
| B4 | An `adapter_entry` element starting `./` resolves against the plugin directory and MUST NOT resolve outside it. |
| B5 | An `adapter_entry` element starting `../` or containing `/../` is refused outright. |
| B6 | The platform appends exactly two arguments, in order: `--request <abs path>` then `--result <abs path>`. The adapter MUST accept them. |
| B7 | A `supported_arch` value not matching the host causes `resolve` to fail with `does not support architecture ...` before anything is launched. |

Minimal valid manifest:

```json
{
  "schema_version": 3,
  "plugin_id": "my-capability",
  "plugin_version": "1.0.0",
  "adapter_entry": ["/opt/team-x/venv/bin/python", "./adapter.py"],
  "capabilities": ["my.capability"],
  "supported_arch": ["aarch64", "x86_64"]
}
```

---

## C. Provenance `provenance.json` (optional)

Fields, all optional, all strings. Unknown fields are a hard error.

`license`, `source_url`, `source_commit`, `notes`

| ID | Rule |
| --- | --- |
| C1 | Absent file = unstated. Not an error. |
| C2 | Malformed or unreadable file is a validation error. |
| C3 | `source_commit` is compared with the platform's `approved_commit`. If both are present and differ, loading FAILS: `declares source_commit X but the admitted revision is Y`. |
| C4 | Provenance grants nothing. It cannot admit a plugin. |

---

## D. Admission record `admissions_root/<plugin_id>.json` (platform-owned)

Written by the platform, not the plugin. Fields: `plugin_id`, `status`,
`license_review`, `approved_commit`, `reviewer`, `reason`. Unknown fields are a
hard error.

| ID | Rule |
| --- | --- |
| D1 | `status` ∈ {`admitted`, `source-audit-only`, `unknown`}. Only `admitted` executes. |
| D2 | `status == "admitted"` REQUIRES `license_review` ∈ {`green`, `yellow`} and a non-empty `approved_commit`. |
| D3 | `status != "admitted"` REQUIRES a non-empty `reason`. |
| D4 | No record for a plugin ⇒ status `unknown`, listed in the catalogue, resolves only with `require_admitted=False`. |
| D5 | `plugin_id` inside the record MUST equal the record's filename stem. |
| D6 | No `admissions_root` configured ⇒ every plugin is unreviewed. |

---

## E. Result `adapter_result.json`

Written by the adapter before it exits. Unknown fields are a hard error.

| Field | Type | Req | Constraint |
| --- | --- | --- | --- |
| `schema_version` | int | Y | MUST equal 3. |
| `status` | str | Y | ∈ {`succeeded`, `failed`, `cancelled`, `timed_out`}. |
| `exit_code` | int | Y | MUST equal the process exit code. `status == "succeeded"` ⇒ MUST be 0. |
| `started_at` | str | Y | Non-empty ISO-8601. |
| `ended_at` | str | Y | Non-empty ISO-8601. |
| `artifacts` | list[obj] | N | See §F. Each needs string `kind` and `path`. |
| `metrics` | list[obj] | N | See §G. |
| `failure` | obj | N | See §H. |
| `provenance` | obj | N | Free-form mapping, stored not interpreted. |

| ID | Rule |
| --- | --- |
| E1 | If the process is cancelled or times out, the platform records `cancelled`/`timed_out` and IGNORES the result file. |
| E2 | Process exited without writing the result ⇒ `protocol_error`, run failed. |
| E3 | `status == "succeeded"` with a non-zero process exit ⇒ `protocol_error`. |
| E4 | `exit_code` ≠ process exit code ⇒ `protocol_error`. |
| E5 | Reserved artifact kinds, refused for adapters: `runtime_protocol_receipt`, `protected_evaluation`. |
| E6 | Reserved metric-context keys an adapter MUST NOT set: `runtime_authority`, `official_qor`. |

---

## F. Artifacts

Object: `{"kind": str, "path": str, "required": bool, "media_type": str|null, "metadata": obj}`

| ID | Rule |
| --- | --- |
| F1 | `path` MUST be relative to the attempt workspace. Absolute paths and any `..` segment are refused. |
| F2 | `kind` MUST appear in the manifest's `artifact_rules`. |
| F3 | File MUST exist with size > 0. A zero-byte file counts as absent. |
| F4 | When `status == "succeeded"`, every manifest rule with `required: true` MUST have a matching declared artifact that satisfies F3. Missing ⇒ `required artifact kinds missing: ...`. A `failed` result is NOT held to this: partial evidence is still evidence. |
| F5 | The platform hashes the file from disk after the process exits. A declared hash is not read. |
| F6 | Artifact paths are stored as workspace-relative keys. The bytes are copied into a content-addressed object store at that moment; the workspace copy is scratch afterwards, and identical bytes from another attempt share the one object. |
| F7 | The reserved kind `runtime_input_manifest` may not be declared by an adapter, only by the platform. |

---

## G. Metrics

Object: `{"name": str, "value": scalar, "unit": str|null, "context": obj}`

An adapter does not know artifact ids — the platform assigns them — so an adapter
sources a metric by naming the *file*:

```json
{"name": "parsed_paths", "value": 4, "unit": "count",
 "context": {"source_artifact_store_key": "timing_paths.index.json",
             "parser_id": "my-parser", "parser_version": "1"}}
```

| ID | Rule |
| --- | --- |
| G1 | `value` MUST be a JSON scalar (number, string, boolean). `NaN` is refused. |
| G2 | `name` and `unit` MUST match IDENTIFIER. |
| G3 | `context.source_artifact_store_key`, when present, MUST name a workspace-relative path that the adapter also declared as an artifact and that was registered. The platform resolves it to the artifact id; an unresolvable key is a FAILURE of the run, not a warning. |
| G4 | `context.parser_id` and `context.parser_version` are lifted onto the metric. |
| G5 | Without a source key the metric is stored and labelled **unsourced**: kept, not hidden, not treated as evidence. |
| G6 | Metrics are registered ONLY for `status == "succeeded"`. |

---

## H. Failure

Object: `{"category": str, "message": str, "retryable": bool, "detail": obj}`

| ID | Rule |
| --- | --- |
| H1 | `category` MUST match IDENTIFIER and SHOULD be a stable word (`configuration_error`, `tool_error`, `transient_error`, ...), because it is recorded and filterable. |
| H2 | `message` MUST be non-empty. It is what an operator reads; name the actual path or value. |
| H3 | `retryable: true` requests another attempt. The platform grants it while `attempt_number < task.max_attempts`, then fails the run. |
| H4 | `retryable` absent or false ⇒ no retry. |
| H5 | The platform never retries on its own. `timed_out`, `cancelled`, `lost` and `protocol_error` are final regardless of `retryable`. |
| H6 | A plugin SHOULD NOT mark a deterministic failure retryable: the budget is finite and a repeat of a deterministic failure learns nothing. |

---

## I. Progress envelopes

One JSON object per line on stdout, prefixed with `manifest.progress_marker`.

```
[progress] {"stage": "...", "phase": "started"}
[progress] {"stage": "...", "phase": "finished", "status": "succeeded", "seconds": 7.9}
```

| Field | Req | Constraint |
| --- | --- | --- |
| `stage` | Y | Non-empty string. Opaque to the platform: any vocabulary, no check. |
| `phase` | Y | ∈ {`started`, `finished`}. |
| `status` | with `finished` | ∈ {`succeeded`, `failed`, `cancelled`}. |
| `seconds` | N | Number. |
| `detail` | N | String. |

| ID | Rule |
| --- | --- |
| I1 | The payload after the marker MUST be ≤ 4096 bytes and valid JSON object. Violations are counted as `progress.malformed` and the run CONTINUES. |
| I2 | A line not starting with the marker is ordinary output. No error, no event. |
| I3 | Duplicate `started` for one stage ⇒ one event. |
| I4 | Emitting no envelopes is valid. The run is recorded as having reported no stages. |
| I5 | Envelopes never determine the outcome. |

---

## J. Runtime environment and lifecycle

| ID | Rule |
| --- | --- |
| J1 | Working directory = a fresh attempt workspace, created before launch, unique per attempt. |
| J2 | Request file is written atomically (temp + rename) before the process starts. |
| J3 | Environment passed: `HOME`, `LANG`, `LC_ALL`, `TZ`, `TMPDIR`, `TEMP`, `TMP` (those present on the host), plus `manifest.environment`, plus `OPENROAD_PLATFORM_PLUGIN_ID` and `OPENROAD_PLATFORM_PLUGIN_VERSION`. Nothing else is inherited. |
| J4 | The platform MUST NOT add itself to the child's `PATH` or import path. |
| J5 | stdout and stderr are appended to `LOG_FILENAME` in the workspace. |
| J6 | Deadline = `min(task.timeout_seconds, manifest.default_timeout_seconds)`. On expiry the process **and all descendants** are terminated. |
| J7 | Cancellation terminates the process group. The adapter is not required to handle a signal; it must not assume it will run to completion. |
| J8 | A retried attempt gets a new workspace and a new attempt number. Files from the previous attempt are NOT visible in the new workspace. |
| J9 | Files declared in `task.staged_inputs` are copied into the workspace before the request is written, and `input_manifest.json` is written at its root. |

---

## K. Front matter of a run

```json
{
  "context": "the run is one attempt of one stage of one run"
}
```

`task` fields the adapter receives: `schema_version`, `task_id`, `project_id`,
`design_id`, `plugin_id`, optional `plugin_version`, `inputs`, `parameters`, `staged_inputs`, `resources`,
`timeout_seconds`, `max_attempts`, `expected_artifacts`, `labels`. `inputs` and
`parameters` are the plugin's own; the platform carries them and does not
validate their shape.

### Staged inputs (`staged_inputs`)

Optional list. Each entry: `{"destination": <relative workspace path>,
"source": <absolute host path> | "artifact_id": <id> | "input_id": <id>, "required": <bool,
default true>}`. Exactly one of `source` / `artifact_id` / `input_id` is given.

The platform copies the bytes to `destination` inside the workspace **before**
the process starts, and digests the copy. For an `artifact_id` the bytes come
from the platform's content-addressed store and are verified against that
artifact's record as they land; a mismatch fails the attempt. The adapter reads
the file at `destination` relative to its working directory; it MUST NOT search
the host for it, and MUST NOT assume the source path is reachable.

The platform writes `input_manifest.json` at the workspace root when the list is
non-empty:

```json
{"schema_version": 3,
 "inputs": [{"destination": "design/netlist.v", "present": true,
             "size_bytes": 4096, "sha256": "<64 hex>"}]}
```

`source` is deliberately absent from the manifest: it is an identity document,
and two runs that read the same bytes into the same destinations are the same
design even if the files lived in different places. The source is recorded by the
platform, not by the manifest.

| ID | Rule |
| --- | --- |
| K1 | Inputs the adapter cannot use ⇒ fail with `configuration_error`. Do not guess, do not substitute a default silently. |
| K2 | `expected_artifacts` lists kinds the task requires. Their presence is enforced as in F4. |
| K3 | An entry with `required: false` whose source is absent MUST NOT fail the attempt; the manifest records `"present": false` and `"sha256": null`. |
| K4 | The adapter MUST NOT write to `input_manifest.json` or `runtime_protocol_receipt.json`. Both are hashed before launch and re-hashed after exit; a mismatch fails the attempt. |
| K5 | A digest in `input_manifest.json` is the platform's measurement and MAY be cited in the result's `provenance`. The adapter's own digest of the same file SHOULD agree with it. |
| K6 | `resources` (optional object: `cpu_cores`, `cpu_seconds`, `memory_bytes`, `processes`) bounds the **whole process tree** the adapter starts, not each process. `cpu_cores` governs the reservation and whether the attempt is admitted; `cpu_seconds` governs when it is stopped; `memory_bytes` governs both; `processes` governs only the stop threshold. A task declaring no `resources` is still reserved at 1 core / 1 GiB. |
| K8 | An attempt that does not fit the machine's budget is NOT started: the run stays `queued`. A `start_attempt` that returns nothing is not an error. |
| K10 | A capability that sets `requirements.resumable: true` is telling the platform it can continue in a workspace it was already given. On a lost lease the run returns to the queue and the next attempt reuses that workspace; otherwise the run is marked `LOST`. A lost lease counts against `max_attempts`. |
| K9 | A caller may request a retry of a failed run whose latest failure was `retryable` and whose attempt budget has room. The task is NOT modified and earlier attempts are kept. |
| K7 | On a breach the attempt is `failed` with `failure.category == "resource_exceeded"`; the adapter's result file is NOT read and the attempt is NOT retried. An adapter that spawns a helper MUST count the helpers against `processes`. |

---

## L. Validation checklist

Run before submitting a plugin to a platform owner.

```
[ ] manifest parses as JSON; schema_version == 3
[ ] every required manifest field present and non-empty
[ ] no unknown manifest fields
[ ] plugin_id matches IDENTIFIER; plugin_version likewise
[ ] adapter_entry[0] is absolute, or is a ./ path inside the plugin dir
[ ] no ../ or /../ anywhere in adapter_entry
[ ] adapter accepts --request <path> --result <path>
[ ] the adapter writes adapter_result.json at exactly the --result path
[ ] result.schema_version == 3
[ ] result.status in {succeeded, failed, cancelled, timed_out}
[ ] process exit code == result.exit_code
[ ] status == succeeded  =>  exit_code == 0
[ ] every artifact path is relative, has no "..", and exists non-empty
[ ] every declared artifact kind is in manifest.artifact_rules
[ ] if status == succeeded: every manifest rule with required:true has a
    declared, non-empty artifact
[ ] an ordinary adapter does not declare a platform-reserved kind in its result
[ ] if task.staged_inputs is non-empty: the adapter reads each file at its
    destination, relative to cwd, and never at the source path
[ ] the adapter never writes input_manifest.json or
    runtime_protocol_receipt.json
[ ] no metric context key is runtime_authority or official_qor
[ ] every metric value is a JSON scalar and not NaN
[ ] failure (when present) has a non-empty message and an identifier category
[ ] retryable is set only where a retry could actually succeed
[ ] if the task declares `resources.processes`, every helper process the
    adapter starts is counted, not just the adapter itself
[ ] provenance.json (if present) declares only the four allowed fields
[ ] the plugin runs correctly with a hand-written request file, with no
    AgenticEDA package installed and no platform on sys.path
```

Standalone check, no platform involved:

```
write  request.json   = {"protocol_version":1,"plugin":{...},"task":{...}}
mkdir  /tmp/ws && cd /tmp/ws
<adapter_entry...> --request <abs>/request.json --result /tmp/ws/adapter_result.json
echo $?                      # must equal the result file's exit_code
cat /tmp/ws/adapter_result.json
```

---

## M. Known gaps

Recorded so they are not mistaken for specified behaviour.

| Gap | Effect |
| --- | --- |
| No compatibility negotiation | The request carries `protocol_version`; a plugin cannot declare a range, and the platform cannot pre-screen an incompatible plugin. A plugin SHOULD fail with `configuration_error` naming both versions if it cannot speak the one it receives. |
| No transport declaration | The transport is the file protocol defined here; it is not a manifest field. |
| No permission model | A plugin process is not sandboxed for network or filesystem access. The manifest cannot declare either. |
| No disk or file-descriptor bounds | `resources` bounds CPU, resident memory and process count. Total disk use, open file descriptors and network are not bounded. |
| Polling, not a sandbox | A limit is measured while the attempt runs, so a spike inside one polling interval is missed. Real isolation needs a container backend, which does not exist yet. |
| No retry backoff | A retryable failure is re-queued immediately. There is no `retry_after`. |
| No envelope ordering | A `finished` for a stage that never started is accepted as `finished`. |
