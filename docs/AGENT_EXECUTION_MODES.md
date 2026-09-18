# Agent execution modes

This guide describes what an agent may submit and what the execution foundation
guarantees. It does not prescribe an EDA strategy.

## Capability request

Use the normal TaskSpec and select a Toolkit capability in the opaque input
payload. The agent chooses the parameters and may submit one task for each
experiment point.

```json
{
  "task_id": "place-density-072",
  "project_id": "research",
  "design_id": "gcd",
  "plugin_id": "openroad-toolkit",
  "inputs": {"capability": "place"},
  "parameters": {"density": 0.72},
  "expected_artifacts": ["def", "report"]
}
```

The platform stores the payload unchanged. In the current protocol,
`inputs.capability` is an opaque Toolkit field; the Adapter decides whether the
capability and parameters are valid.

## Script request

Scripts are ordinary agent-authored inputs. They are staged into the attempt
workspace and executed by the Toolkit Adapter.

```json
{
  "plugin_id": "openroad-toolkit",
  "inputs": {
    "capability": "script_execution",
    "script_path": "inputs/place.tcl"
  },
  "staged_inputs": [
    {
      "source": "/srv/experiments/place.tcl",
      "destination": "inputs/place.tcl"
    }
  ]
}
```

The foundation does not execute or inspect TCL, Python, or shell semantics. The
Toolkit Adapter executes the script. The foundation validates the staged path,
creates the workspace, measures the input, controls the process tree, and
records the result.

## Patch, build, and benchmark

An agent may stage a patch and build configuration as inputs to a Toolkit
capability. The Toolkit Adapter applies, builds, benchmarks, and evaluates the
inputs. Build, benchmark, and evaluation are separate Tasks when the agent
needs separate evidence or comparison points.

```text
patch task → build task → benchmark task → evaluation task
```

Artifact bindings can transfer the patch, build output, netlist, reports, or
other measured files between Tasks. The foundation does not interpret the patch
or decide whether the benchmark is scientifically useful.

## Evidence returned to the agent

For every Task, query the run and its child attempt for:

- input and toolchain hashes;
- workspace and attempt identity;
- process timeline and bounded logs;
- artifact hashes and metadata;
- metrics and their source artifacts;
- resource measurements;
- failure source, category, message, and retryability.

`execution_valid` means that the requested execution completed with the
declared evidence. It does not mean that the design strategy or QoR is good;
that interpretation remains with the Toolkit, evaluator, data foundation, or
agent according to the experiment contract.
