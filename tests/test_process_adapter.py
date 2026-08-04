from __future__ import annotations

import sys
from pathlib import Path

from openroad_platform_contracts import PluginManifest, RuntimeStatus, TaskSpec
from openroad_platform_execution import PluginRegistry
from openroad_platform_execution.adapter import ProcessAdapter
from openroad_platform_execution.process_guardian import ProcessGuardian


FIXTURES = Path(__file__).parent / "fixtures"
EXAMPLES = Path(__file__).parents[1] / "integrations" / "examples"


def manifest(script: str) -> PluginManifest:
    return PluginManifest(
        plugin_id="echo",
        plugin_version="1.0.0",
        adapter_entry=(sys.executable, str(FIXTURES / script)),
        capabilities=("test.echo",),
        supported_arch=("aarch64", "x86_64"),
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        artifact_rules=({"kind": "report", "required": True},),
        default_timeout_seconds=10,
    )


def task() -> TaskSpec:
    return TaskSpec(
        task_id="task-echo", project_id="project", design_id="design",
        plugin_id="echo", inputs={"message": "hello"},
        expected_artifacts=("report",), timeout_seconds=10,
    )


def short_task() -> TaskSpec:
    return TaskSpec(
        task_id="task-short", project_id="project", design_id="design",
        plugin_id="echo", timeout_seconds=1,
    )


def test_process_adapter_validates_and_hashes_workspace_artifacts(tmp_path):
    execution = ProcessAdapter().execute(
        manifest("echo_adapter.py"), task(), workspace=tmp_path / "attempt"
    )
    assert execution.result.status is RuntimeStatus.SUCCEEDED
    assert execution.outcome.returncode == 0
    assert len(execution.artifacts) == 1
    assert execution.artifacts[0]["store_key"] == "report.json"
    assert len(execution.artifacts[0]["sha256"]) == 64


def test_process_adapter_rejects_artifact_path_escape(tmp_path):
    execution = ProcessAdapter().execute(
        manifest("bad_artifact_adapter.py"), task(), workspace=tmp_path / "attempt"
    )
    assert execution.result.status is RuntimeStatus.FAILED
    assert execution.result.failure["category"] == "protocol_error"
    assert "outside" in execution.result.failure["message"]


def test_process_adapter_returns_structured_timeout(tmp_path):
    adapter = ProcessAdapter(ProcessGuardian(poll_interval=0.02, terminate_grace=0.2))
    execution = adapter.execute(
        manifest("sleep_adapter.py"), short_task(), workspace=tmp_path / "timeout"
    )

    assert execution.result.status is RuntimeStatus.TIMED_OUT
    assert execution.result.failure["category"] == "timeout"
    assert execution.outcome.timed_out is True


def test_process_adapter_returns_structured_cancellation(tmp_path):
    adapter = ProcessAdapter(ProcessGuardian(poll_interval=0.02, terminate_grace=0.2))
    execution = adapter.execute(
        manifest("sleep_adapter.py"), task(), workspace=tmp_path / "cancel",
        cancel_requested=lambda: True,
    )

    assert execution.result.status is RuntimeStatus.CANCELLED
    assert execution.result.failure["category"] == "cancelled"
    assert execution.outcome.cancelled is True


def test_process_adapter_rejects_undeclared_artifact_kind(tmp_path):
    restricted = PluginManifest(
        plugin_id="echo", plugin_version="1.0.0",
        adapter_entry=(sys.executable, str(FIXTURES / "echo_adapter.py")),
        capabilities=("test.echo",), supported_arch=("aarch64", "x86_64"),
        input_schema={"type": "object"}, output_schema={"type": "object"},
        artifact_rules=({"kind": "metrics", "required": False},),
        default_timeout_seconds=10,
    )

    execution = ProcessAdapter().execute(
        restricted, task(), workspace=tmp_path / "undeclared-kind"
    )

    assert execution.result.status is RuntimeStatus.FAILED
    assert "not allowed" in execution.result.failure["message"]


def test_repository_echo_manifest_and_adapter_are_conformant(tmp_path):
    example = PluginRegistry.from_directory(EXAMPLES).resolve("echo")

    execution = ProcessAdapter().execute(
        example, task(), workspace=tmp_path / "repository-example"
    )

    assert execution.result.status is RuntimeStatus.SUCCEEDED
    assert execution.artifacts[0]["kind"] == "report"
