# Research Toolkit example

This is a small external-process Toolkit example, not kernel logic. It proves
that an Agent can submit either an executable script or a source patch through
the normal TaskSpec while the Execution Foundation owns the workspace,
process, lifecycle, input digest, artifact, metric, and failure evidence.

The Adapter implements two Toolkit-owned capabilities:

- `script`: execute an Agent-provided Python script;
- `patch_benchmark`: apply a patch, compile, run a baseline and candidate, and
  write a comparison report.

The platform does not interpret or authorize these capability names. A real
Toolkit must provide its own manifest, admission record, environment, tool
preflight, and domain validation when installed on a host.

The end-to-end coverage is in
`apps/plan_executor/test_against_kernel.py::test_agent_generated_code_is_executed_and_measured`.
