# Quickstart: a real Agent-authored execution

[中文版本](README.zh-CN.md)

This is the shortest path for a new contributor. It uses the repository's
Research Toolkit and the real gateway, worker and plan executor. It does not
need OpenROAD, a license server or a vendor installation.

## Run it

From the repository root, install the local packages first:

~~~bash
tools/install-local.sh
python3 -m pytest -q \
  apps/plan_executor/test_against_kernel.py \
  -k test_agent_generated_code_is_executed_and_measured
~~~

The parametrized test runs three cases:

- `script`: an Agent-authored Python script writes a report;
- `patch_benchmark`: an Agent-authored patch changes a small C program, then
  the adapter builds and compares baseline and candidate;
- `build_failure`: the candidate does not compile and the platform records a
  Toolkit build failure.

The test creates temporary plugin, admission and state directories. It starts
HTTP services and a worker, submits a Plan, waits for a terminal state, and
checks the recorded artifact, metric, input digest, log and timeline. It
cleans up the services at the end.

## What to copy for a real Toolkit

The test is intentionally self-contained, but the Toolkit implementation to
copy is [Research Toolkit](../research-toolkit/). Its adapter shows:

- how to read the immutable request;
- how to accept Agent parameters and staged files;
- how to run an Agent script;
- how to apply a patch and run a benchmark;
- how to write artifact and metric evidence;
- how to classify configuration, patch, build and benchmark failures.

After copying it, replace its capability names and tool commands. Keep the
platform boundary: the adapter owns tool semantics, while the platform owns
workspace, process, timeout, cancellation, artifact registration and evidence.

## What success means

A passing test proves that the execution lifecycle works for this Toolkit
shape. It does not prove that a real EDA tool is installed or that a design
meets a physical signoff target. For that, use the external ORFS Toolkit and
the [GCD acceptance record](../../docs/internal/GCD_ACCEPTANCE_2026-09-18.md).
