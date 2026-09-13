"""The adapter protocol: what a plugin may and may not get away with.

Every test here corresponds to a way a plugin could corrupt the platform's
record.  This is the boundary that makes "the upstream project owns its
algorithm" safe to say.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from openroad_platform_contracts import PluginManifest, RuntimeRequirements, TaskSpec
from openroad_platform_runtime.adapter import (
    LOG_FILENAME,
    ProcessAdapter,
    REQUEST_FILENAME,
    RESULT_FILENAME,
)
from openroad_platform_runtime.guardian import ProcessGuardian

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_adapter.py"


def manifest(**overrides) -> PluginManifest:
    base = dict(
        plugin_id="fake-capability",
        plugin_version="1.0.0",
        adapter_entry=(sys.executable, str(FIXTURE)),
        capabilities=("do.thing",),
        supported_arch=("aarch64", "x86_64", "arm64"),
        input_schema={},
        output_schema={},
        artifact_rules=(
            {"kind": "report", "required": True},
            {"kind": "log", "required": False},
        ),
        default_timeout_seconds=60,
    )
    base.update(overrides)
    return PluginManifest(**base)


def task(behaviour: str, **overrides) -> TaskSpec:
    base = dict(
        task_id=f"task-{behaviour}", project_id="p", design_id="d",
        plugin_id="fake-capability",
        inputs={"behaviour": behaviour},
        timeout_seconds=30,
    )
    base.update(overrides)
    return TaskSpec(**base)


@pytest.fixture()
def adapter() -> ProcessAdapter:
    return ProcessAdapter(ProcessGuardian(poll_interval=0.02, terminate_grace=1.0))


def run(adapter: ProcessAdapter, tmp_path: Path, behaviour: str, **task_overrides):
    return adapter.execute(
        manifest(), task(behaviour, **task_overrides), workspace=tmp_path,
    )


# --------------------------------------------------------------------------
# the happy path
# --------------------------------------------------------------------------

def test_a_successful_run_yields_hashed_artifacts(adapter, tmp_path):
    execution = run(adapter, tmp_path, "ok")
    assert execution.result.status.value == "succeeded"
    assert execution.outcome.returncode == 0
    kinds = {a["kind"] for a in execution.artifacts}
    assert kinds == {"report", "log"}
    report = next(a for a in execution.artifacts if a["kind"] == "report")
    import hashlib

    assert report["sha256"] == hashlib.sha256(
        (tmp_path / "report.json").read_bytes()
    ).hexdigest()
    assert report["size_bytes"] > 0


def test_the_request_file_carries_the_plugin_identity_and_task(adapter, tmp_path):
    import json

    run(adapter, tmp_path, "ok")
    request = json.loads((tmp_path / REQUEST_FILENAME).read_text(encoding="utf-8"))
    assert request["plugin"]["plugin_id"] == "fake-capability"
    assert request["task"]["task_id"] == "task-ok"
    assert (tmp_path / RESULT_FILENAME).is_file()
    assert (tmp_path / LOG_FILENAME).is_file()


def test_the_adapter_only_sees_an_allowlisted_environment(adapter, tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_SECRET_TOKEN", "must-not-leak")
    seen: list[str] = []
    adapter.execute(
        manifest(environment={"DECLARED_BY_PLUGIN": "yes"}),
        task("ok"),
        workspace=tmp_path,
        on_line=seen.append,
    )
    # The plugin's own declared variable and the platform identity are present;
    # an unrelated host secret is not reachable through the protocol.
    from openroad_platform_runtime.adapter import ProcessAdapter as PA

    env = PA._environment(manifest(environment={"DECLARED_BY_PLUGIN": "yes"}))
    assert env["DECLARED_BY_PLUGIN"] == "yes"
    assert env["OPENROAD_PLATFORM_PLUGIN_ID"] == "fake-capability"
    assert "PLATFORM_SECRET_TOKEN" not in env


# --------------------------------------------------------------------------
# a plugin cannot lie about its own result
# --------------------------------------------------------------------------

def test_success_with_a_nonzero_exit_code_is_a_protocol_failure(adapter, tmp_path):
    execution = run(adapter, tmp_path, "lie_success_nonzero_exit")
    assert execution.result.status.value == "failed"
    assert execution.result.failure["category"] == "protocol_error"
    assert "non-zero process exit code" in execution.result.failure["message"]


def test_a_result_mismatching_the_process_exit_code_is_refused(adapter, tmp_path):
    execution = run(adapter, tmp_path, "exit_code_mismatch")
    assert execution.result.status.value == "failed"
    assert execution.result.failure["category"] == "protocol_error"
    assert "does not match" in execution.result.failure["message"]


def test_no_result_file_is_a_protocol_failure(adapter, tmp_path):
    execution = run(adapter, tmp_path, "no_result")
    assert execution.result.status.value == "failed"
    assert "produced no" in execution.result.failure["message"]


def test_an_honest_failure_is_preserved_as_a_failure(adapter, tmp_path):
    execution = run(adapter, tmp_path, "fail")
    assert execution.result.status.value == "failed"
    assert execution.result.exit_code == 2
    assert execution.result.failure["category"] == "tool_error"


# --------------------------------------------------------------------------
# artifacts
# --------------------------------------------------------------------------

def test_an_artifact_path_may_not_escape_the_workspace(adapter, tmp_path):
    execution = run(adapter, tmp_path, "escape_workspace")
    assert execution.result.status.value == "failed"
    assert execution.result.failure["category"] == "protocol_error"
    message = execution.result.failure["message"].lower()
    assert "outside.txt" in message
    assert "workspace" in message
    # The escape must not have been registered as evidence.
    assert execution.artifacts == ()


def test_a_declared_but_absent_artifact_is_refused(adapter, tmp_path):
    execution = run(adapter, tmp_path, "missing_artifact")
    assert execution.result.status.value == "failed"
    assert "missing or empty" in execution.result.failure["message"]


def test_a_kind_outside_the_manifest_allowlist_is_refused(adapter, tmp_path):
    execution = run(adapter, tmp_path, "disallowed_kind")
    assert execution.result.status.value == "failed"
    assert "not allowed by the manifest" in execution.result.failure["message"]


def test_a_required_artifact_kind_must_actually_be_produced(adapter, tmp_path):
    execution = adapter.execute(
        manifest(), task("ok", expected_artifacts=("provenance",)),
        workspace=tmp_path,
    )
    assert execution.result.status.value == "failed"
    assert "required artifact kinds missing" in execution.result.failure["message"]


def test_a_plugin_may_not_forge_protected_evaluator_authority(adapter, tmp_path):
    execution = run(adapter, tmp_path, "forge_authority")
    # The adapter protocol itself accepts the artifact; the runtime is what
    # refuses forged authority, so this asserts the artifact survives here...
    assert execution.result.status.value == "succeeded"
    forged = execution.result.artifacts[0]["metadata"]
    assert forged.get("official_qor") == 1.0
    # ...and that the runtime's guard is what stops it (see test_runtime.py).


# --------------------------------------------------------------------------
# timeouts
# --------------------------------------------------------------------------

def test_the_adapter_deadline_is_the_smaller_of_task_and_manifest(tmp_path):
    adapter = ProcessAdapter(ProcessGuardian(poll_interval=0.02, terminate_grace=1.0))
    execution = adapter.execute(
        manifest(default_timeout_seconds=1),
        task("noisy", timeout_seconds=600),
        workspace=tmp_path,
    )
    assert execution.result.status.value == "timed_out"
    assert execution.outcome.timed_out is True


def test_a_noisy_adapter_still_times_out(tmp_path):
    adapter = ProcessAdapter(ProcessGuardian(poll_interval=0.01, terminate_grace=1.0))
    execution = adapter.execute(
        manifest(default_timeout_seconds=60),
        task("noisy", timeout_seconds=1),
        workspace=tmp_path,
    )
    assert execution.result.status.value == "timed_out"


def test_cancellation_produces_a_cancelled_result(tmp_path):
    adapter = ProcessAdapter(ProcessGuardian(poll_interval=0.02, terminate_grace=1.0))
    execution = adapter.execute(
        manifest(default_timeout_seconds=60),
        task("noisy", timeout_seconds=600),
        workspace=tmp_path,
        cancel_requested=lambda: True,
    )
    assert execution.result.status.value == "cancelled"
    assert execution.result.failure["category"] == "cancelled"


# --------------------------------------------------------------------------
# mis-wiring
# --------------------------------------------------------------------------

def test_a_task_for_another_plugin_is_refused(adapter, tmp_path):
    with pytest.raises(ValueError, match="but the manifest is"):
        adapter.execute(
            manifest(), task("ok", plugin_id="different-capability"),
            workspace=tmp_path,
        )


def test_a_progress_marker_is_declared_by_the_manifest_not_assumed():
    default = manifest()
    custom = manifest(progress_marker="<<stage>>")
    assert default.progress_marker == "[progress]"
    assert custom.progress_marker == "<<stage>>"
