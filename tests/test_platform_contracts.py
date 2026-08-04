from __future__ import annotations

import pytest

from openroad_platform_contracts import (
    ActionProposal,
    Event,
    PluginManifest,
    PluginResult,
    RuntimeStatus,
    TaskSpec,
)


def test_task_spec_round_trip_and_version_gate():
    task = TaskSpec(
        task_id="task-001",
        project_id="project-1",
        design_id="design-1",
        plugin_id="echo",
        inputs={"message": "hello"},
        timeout_seconds=30,
        max_attempts=2,
        expected_artifacts=("report",),
    )
    task.validate()
    assert TaskSpec.from_dict(task.to_dict()) == task

    payload = task.to_dict()
    payload["schema_version"] = 2
    with pytest.raises(ValueError, match="schema_version"):
        TaskSpec.from_dict(payload)
    payload.pop("schema_version")
    with pytest.raises(ValueError, match="requires schema_version"):
        TaskSpec.from_dict(payload)


def test_task_spec_requires_exactly_one_execution_target():
    with pytest.raises(ValueError, match="exactly one"):
        TaskSpec(task_id="t", project_id="p", design_id="d").validate()
    with pytest.raises(ValueError, match="exactly one"):
        TaskSpec(
            task_id="t", project_id="p", design_id="d",
            plugin_id="echo", workflow_id="flow",
        ).validate()


def test_plugin_manifest_and_result_are_strictly_versioned():
    manifest = PluginManifest.from_dict({
        "schema_version": 1,
        "plugin_id": "echo",
        "plugin_version": "1.0.0",
        "adapter_entry": ["python3", "echo_adapter.py"],
        "capabilities": ["test.echo"],
        "supported_arch": ["aarch64"],
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "required_tools": [],
        "default_timeout_seconds": 30,
        "artifact_rules": [{"kind": "report", "required": True}],
        "environment": {},
    })
    assert manifest.adapter_entry == ("python3", "echo_adapter.py")

    result = PluginResult(
        status=RuntimeStatus.SUCCEEDED,
        exit_code=0,
        started_at="2026-08-04T00:00:00+00:00",
        ended_at="2026-08-04T00:00:01+00:00",
        artifacts=({"kind": "report", "path": "report.json"},),
    )
    result.validate()
    assert PluginResult.from_dict(result.to_dict()) == result


def test_proposal_and_event_round_trip():
    proposal = ActionProposal(
        proposal_id="proposal-1",
        producer="agenticpd",
        target_run_id="run-1",
        action_type="submit_child_run",
        parameters={"PLACE_DENSITY": 0.55},
        evidence_refs=("artifact-1",),
        risk="medium",
        budget={"max_runs": 1},
    )
    event = Event(
        event_id="event-1",
        run_id="run-1",
        event_type="attempt.started",
        occurred_at="2026-08-04T00:00:00+00:00",
        producer="runtime",
        payload={"attempt_id": "attempt-1"},
    )
    assert ActionProposal.from_dict(proposal.to_dict()) == proposal
    assert Event.from_dict(event.to_dict()) == event
