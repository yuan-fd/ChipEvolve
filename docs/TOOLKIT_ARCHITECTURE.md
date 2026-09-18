# Toolkit architecture

AgenticEDA is an execution environment for agents, not a pre-defined EDA flow
engine. The agent owns the intelligent work: understanding the goal, choosing
capabilities, designing experiments, selecting parameters, composing actions,
generating scripts, and preparing source patches. The execution foundation owns
the engineering work needed to run those decisions reliably.

## The boundary

```text
Agent
  | intent, capability, parameters, scripts, code, patches
  v
Execution Foundation
  | workspace, resources, processes, lifecycle, evidence
  v
Toolkit Adapter
  | tool commands, environment, parsers, artifact extraction
  v
EDA tool
```

The rule is not “an agent cannot call a Toolkit”. The rule is:

> An agent must not bypass the Execution Foundation to operate a tool.

The foundation does not choose the next EDA action, alter agent parameters, or
interpret placement, CTS, routing, or timing strategy.

## Terms

- **Toolkit**: a capability set for one EDA tool or toolchain.
- **Capability**: an operation exposed by a Toolkit, such as `place`, `route`,
  `timing`, or `script_execution`.
- **Adapter**: the Toolkit's process boundary with the foundation.
- **Plugin**: the external package used to discover, version, admit, and run a
  Toolkit. The existing `plugin_id` remains the wire identity for compatibility.
- **Task**: one agent-authored execution request.
- **Plan**: an agent-authored ordered set of Tasks and artifact bindings.

The platform currently keeps `inputs` and `parameters` opaque. A Toolkit owns
their meaning and rejects values it cannot use; the foundation only validates
the execution envelope, paths, resources, and lifecycle constraints.

## Three execution modes

### Capability experiment

An agent chooses a Toolkit capability and supplies domain parameters:

```json
{
  "plugin_id": "openroad-toolkit",
  "inputs": {"capability": "place"},
  "parameters": {"density": 0.72}
}
```

### Agent-authored script

An agent stages a script and asks the Toolkit to execute it. The foundation
records the script bytes, workspace, process, environment, logs, and outputs;
it does not parse the script or decide what it means.

### Agent-authored patch and benchmark

An agent stages a patch and build configuration. A Toolkit performs build,
benchmark, and evaluation as ordinary Tasks. The patch, toolchain snapshot,
build logs, reports, and metrics remain linked evidence that can be compared to
another run.

## Toolkit contract

A Toolkit package declares its identity and capabilities, adapter entry point,
environment requirements, artifact rules, and progress format. Its adapter
reports failure categories, while its provenance and toolchain snapshot provide
the version evidence. It may expose both high-level capabilities and a
low-level script entry point. The foundation does not require an agent to use a
high-level flow.

The existing plugin protocol is the transport and trust contract. Toolkit is
the capability organization model; it is not a second execution protocol.

## Responsibilities

| Agent | Execution Foundation | Toolkit Adapter |
| --- | --- | --- |
| Understands goals and state | Creates attempt workspaces | Maps capability to tool commands |
| Designs experiments | Stages and hashes inputs | Describes tool environment |
| Chooses parameters and actions | Manages resources and processes | Runs scripts or flows |
| Generates scripts and patches | Handles timeout, cancel, retry | Parses tool outputs |
| Decides how to compare runs | Stores logs, artifacts, metrics | Declares artifacts and failures |

This separation reduces agent context load without reducing agent authority.
