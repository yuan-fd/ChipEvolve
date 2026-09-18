# Research Toolkit example

[中文版本](README.zh-CN.md)

This directory is a small external-process Toolkit example. It is deliberately
not an EDA tool. Its job is to show the shape that a real OpenROAD, Innovus or
PrimeTime Toolkit can follow without importing platform code.

It provides two Toolkit-owned capabilities:

- `script`: execute an Agent-provided Python script;
- `patch_benchmark`: apply an Agent-provided source patch, build a baseline and
  candidate, run both, and write a comparison report.

The platform does not interpret these capability names or parameters. It only
stages files, starts the adapter, supervises the process, hashes artifacts and
stores the evidence.

## Run the end-to-end example

From the repository root:

~~~bash
tools/install-local.sh
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest -q \
  apps/plan_executor/test_against_kernel.py \
  -k test_agent_generated_code_is_executed_and_measured
~~~

This is a real local integration test, not a direct invocation of
`adapter.py`. It creates an admission record, launches the gateway, worker and
plan executor, submits an Agent-authored plan, and queries the result through
the platform boundary.

The three parameterized cases are:

| Case | Agent input | Expected evidence |
| --- | --- | --- |
| `script` | staged Python script and `parameters.seed` | report artifact and `result_value` metric |
| `patch_benchmark` | source, patch, compiler and patch tool | baseline/candidate report and metric |
| `build_failure` | patch producing invalid C | Toolkit `build_error`, failed plan and logs |

## Package shape

~~~text
research-example/
├── research-example.plugin.json
├── provenance.json                 optional
└── adapter.py                      accepts --request and --result
~~~

The manifest declares schema version, identity, architecture, capabilities and
artifact rules. The adapter is started in an attempt workspace. It reads the
request's task, writes outputs relative to the workspace, and writes a result
whose exit code agrees with the process.

The platform-owned admission record is kept outside the Toolkit directory.
The test creates one automatically; a deployed Toolkit needs a reviewed
record under the configured admissions root.

## Adapt it to an EDA tool

1. Replace `capabilities` with names meaningful to your toolchain.
2. Keep domain parameters in `task.inputs` and `task.parameters`.
3. Resolve tool executables and libraries in the Toolkit environment; do not
   add their names to the kernel.
4. Translate tool output into declared artifacts and metrics.
5. Make every metric point to the report artifact that supports it.
6. Return a structured failure for invalid input, tool failure, build failure
   and evaluation failure.
7. Add a real Plan-level acceptance test with one success and one failure.
8. Record the tool and Toolkit revisions in provenance.
Read the [protocol](../../docs/PLUGIN_PROTOCOL.human.md) for exact refusal rules
and the [architecture overview](../../docs/ARCHITECTURE_OVERVIEW.md) for
ownership decisions.
