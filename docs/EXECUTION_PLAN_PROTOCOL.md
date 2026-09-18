# Execution plan protocol

The plan service accepts a small ordered task list authored by the Agent. It is
deliberately serial: each submitted step starts only after the previous step
succeeds. The service executes the Agent's plan; it does not choose an EDA flow,
interpret capability parameters, or prevent a step from carrying an
Agent-generated script or patch.

## Request

```json
{
  "plan_id": "optional-stable-id",
  "steps": [
    {
      "step_id": "produce",
      "task": {
        "schema_version": 3,
        "task_id": "unique-task-id",
        "project_id": "project",
        "design_id": "design",
        "plugin_id": "plugin",
        "inputs": {},
        "parameters": {},
        "staged_inputs": [],
        "resources": {
          "cpu_cores": 4,
          "memory_bytes": 8589934592
        },
        "timeout_seconds": 7200,
        "max_attempts": 1,
        "expected_artifacts": ["report"]
      }
    },
    {
      "step_id": "consume",
      "task": {
        "schema_version": 3,
        "task_id": "unique-consumer-task",
        "project_id": "project",
        "design_id": "design",
        "plugin_id": "consumer",
        "inputs": {}
      },
      "bindings": [
        {
          "from_step": "produce",
          "artifact_kind": "report",
          "destination": "inputs/report.json",
          "metadata": {"format": "json"}
        }
      ]
    }
  ]
}
```

`plan_id` and every `step_id` are identifiers of at most 128 characters. A plan
contains 1 to 64 steps. Each binding may refer only to an earlier step and must
match exactly one registered artifact. `metadata` is an exact-match selector
over opaque artifact metadata. The selected artifact is staged by artifact ID,
so the kernel copies and measures it; the plan service never copies files.

The nested `task` is the normal immutable TaskSpec. The plan service does not
reinterpret it. Resource admission, attempt retries, workspace creation,
process control, artifact hashing and evidence remain kernel-owned. The
`plugin_id` identifies the external Toolkit package for compatibility; its
capabilities and domain payload remain Toolkit-owned.

## Response and states

Creation returns HTTP 201 with `{ "plan": ... }`. Querying returns the plan and
ordered steps. Each submitted step gains its durable kernel `run_id`.

Plan states are `queued`, `running`, `succeeded`, `failed`, `cancelled`,
`timed_out` or `lost`. `execution_valid` is:

- `true` only for `succeeded` after all steps succeed;
- `false` for every terminal failure;
- `null` while queued or running.

A failure has:

```json
{
  "source": "platform",
  "category": "resource_exceeded",
  "message": "recorded explanation",
  "retryable": false
}
```

`source=platform` covers protocol, resource, runtime, timeout, lease,
cancellation and submission failures. Other recorded tool failures are
`source=plugin`. A failed step stops all later steps. Cancelling a plan cancels
its active child run and never starts the next step.

## Routes

Direct plan-service routes:

```text
POST /plans
GET  /plans/{plan_id}
POST /plans/{plan_id}/cancel
```

Through the gateway, prefix each route with `/app/plan_executor`. The local
launcher uses the gateway's loopback-only no-auth profile.
