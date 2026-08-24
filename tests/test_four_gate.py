from __future__ import annotations

import sys
from pathlib import Path

import pytest

from openroad_platform_contracts import ActionKind, ActionSpec, PluginManifest, TaskSpec
from openroad_platform_execution import PluginRegistry
from openroad_platform_scheduler import ExperimentGraphStore, FourGateController, RuntimeStore, WorkflowRuntime


FIXTURE = Path(__file__).parent / "fixtures" / "echo_adapter.py"


def _runtime(tmp_path: Path) -> WorkflowRuntime:
    plugin = PluginManifest("echo", "1.0.0", (sys.executable, str(FIXTURE)), ("test.echo",),
                            ("x86_64", "aarch64"), {}, {}, artifact_rules=({"kind": "echo", "required": True},))
    return WorkflowRuntime(RuntimeStore(tmp_path / "runtime.db"), PluginRegistry([plugin]), workspace_root=tmp_path / "runs")


def test_four_gates_require_observation_review_and_terminal_measurement(tmp_path):
    runtime = _runtime(tmp_path); gates = FourGateController(ExperimentGraphStore(tmp_path / "graph.db"), runtime)
    base = TaskSpec("baseline-task", "p", "d", plugin_id="echo", parameters={"knob": 1})
    experiment, baseline_run = gates.begin_baseline(base)
    runtime.execute_once(baseline_run)
    observed = gates.observe_baseline(experiment, baseline_run)
    proposal = gates.propose(experiment, observed, producer="optimizer", payload={"why": "test"}, evidence_refs=(f"run:{baseline_run}",))
    action = ActionSpec("action-1", experiment, proposal, ActionKind.PARAMETER, "test", "test", "stop", "rollback",
                        {"values": {"knob": 2}}, (f"run:{baseline_run}",), "reviewer")
    candidate_run, attempt = gates.review_and_submit(action, base)
    runtime.execute_once(candidate_run)
    measurement = gates.observe_attempt(experiment, attempt)
    decision, memory = gates.decide_and_record_memory(
        experiment, measurement, producer="judge", outcome="no_improvement",
        rationale="The terminal measurement did not beat baseline.", memory_kind="episodic",
        evidence_refs=(f"run:{candidate_run}",),
    )
    graph = gates.graph.describe(experiment)
    assert measurement in {item["node_id"] for item in graph["nodes"]}
    assert {decision, memory} <= {item["node_id"] for item in graph["nodes"]}
    assert any(edge["relation"] == "approved" for edge in graph["edges"])
    assert any(edge["relation"] == "learns" for edge in graph["edges"])


def test_four_gate_rejects_unreviewed_parameter_outside_base_task(tmp_path):
    runtime = _runtime(tmp_path); gates = FourGateController(ExperimentGraphStore(tmp_path / "graph.db"), runtime)
    base = TaskSpec("baseline-task", "p", "d", plugin_id="echo", parameters={"knob": 1})
    experiment, baseline_run = gates.begin_baseline(base); runtime.execute_once(baseline_run)
    observed = gates.observe_baseline(experiment, baseline_run)
    proposal = gates.propose(experiment, observed, producer="optimizer", payload={}, evidence_refs=(f"run:{baseline_run}",))
    action = ActionSpec("action-2", experiment, proposal, ActionKind.PARAMETER, "x", "y", "z", "r",
                        {"values": {"not_allowed": 2}}, (f"run:{baseline_run}",), "reviewer")
    with pytest.raises(ValueError, match="declared TaskSpec"):
        gates.review_and_submit(action, base)
