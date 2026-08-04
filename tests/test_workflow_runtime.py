from __future__ import annotations

import platform
import sys
import threading
import time
from pathlib import Path

from openroad_platform_contracts import PluginManifest, RuntimeStatus, TaskSpec
from openroad_platform_execution import PluginRegistry, ProcessAdapter, ProcessGuardian
from openroad_platform_scheduler import RuntimeStore, WorkflowRuntime


FIXTURES = Path(__file__).parent / "fixtures"


def registry(script: str) -> PluginRegistry:
    return PluginRegistry([PluginManifest(
        plugin_id="echo",
        plugin_version="1.0.0",
        adapter_entry=(sys.executable, str(FIXTURES / script)),
        capabilities=("test.echo",),
        supported_arch=(platform.machine(),),
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        artifact_rules=({"kind": "report", "required": True},),
        default_timeout_seconds=10,
    )])


def task(*, timeout_seconds: int = 10) -> TaskSpec:
    return TaskSpec(
        task_id="task-e2e", project_id="project", design_id="design",
        plugin_id="echo", inputs={"message": "runtime owns status"},
        expected_artifacts=("report",), timeout_seconds=timeout_seconds,
    )


def test_runtime_executes_full_contract_attempt_evidence_chain(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.db")
    runtime = WorkflowRuntime(
        store, registry("echo_adapter.py"),
        workspace_root=tmp_path / "workspaces", worker_id="test-worker",
    )
    run = runtime.submit(task(), capability="test.echo")
    completed = runtime.execute_once(run.run_id)

    assert completed.status is RuntimeStatus.SUCCEEDED
    view = runtime.describe(run.run_id)
    attempt = view["stages"][0]["attempts"][0]
    assert attempt["status"] == "succeeded"
    assert attempt["artifacts"][0]["store_key"] == "report.json"
    assert attempt["metrics"][0]["name"] == "messages"
    assert [event["event_type"] for event in view["events"]] == [
        "run.accepted", "stage.ready", "attempt.started",
        "artifact.registered", "metric.recorded", "attempt.finished", "run.finished",
    ]


def test_runtime_rejects_plugin_success_when_artifact_escapes_workspace(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.db")
    runtime = WorkflowRuntime(
        store, registry("bad_artifact_adapter.py"),
        workspace_root=tmp_path / "workspaces", worker_id="test-worker",
    )
    run = runtime.submit(task())
    failed = runtime.execute_once(run.run_id)

    assert failed.status is RuntimeStatus.FAILED
    attempt = runtime.describe(run.run_id)["stages"][0]["attempts"][0]
    assert attempt["failure"]["category"] == "protocol_error"
    assert attempt["artifacts"] == []


def test_runtime_records_structured_timeout_and_terminal_event(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.db")
    runtime = WorkflowRuntime(
        store, registry("sleep_adapter.py"), workspace_root=tmp_path / "workspaces",
        adapter=ProcessAdapter(ProcessGuardian(
            poll_interval=0.02, terminate_grace=0.2,
        )),
        worker_id="test-worker",
    )
    run = runtime.submit(task(timeout_seconds=1))

    completed = runtime.execute_once(run.run_id)
    view = runtime.describe(run.run_id)

    assert completed.status is RuntimeStatus.FAILED
    assert completed.terminal_reason == "timed_out"
    attempt = view["stages"][0]["attempts"][0]
    assert attempt["status"] == "timed_out"
    assert attempt["failure"]["category"] == "timeout"
    assert view["events"][-2]["payload"]["status"] == "timed_out"
    assert view["events"][-1]["event_type"] == "run.finished"


def test_runtime_cancels_live_process_after_durable_request(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.db")
    runtime = WorkflowRuntime(
        store, registry("sleep_adapter.py"), workspace_root=tmp_path / "workspaces",
        adapter=ProcessAdapter(ProcessGuardian(
            poll_interval=0.02, terminate_grace=0.2,
        )),
        worker_id="test-worker",
    )
    run = runtime.submit(task())
    worker = threading.Thread(target=runtime.execute_once, args=(run.run_id,))
    worker.start()
    deadline = time.monotonic() + 2
    while not store.list_attempts(store.list_stages(run.run_id)[0].stage_run_id):
        if time.monotonic() >= deadline:
            raise AssertionError("runtime did not start an attempt")
        time.sleep(0.01)

    store.request_cancel(run.run_id)
    worker.join(timeout=3)
    view = runtime.describe(run.run_id)

    assert not worker.is_alive()
    assert view["run"]["status"] == "cancelled"
    attempt = view["stages"][0]["attempts"][0]
    assert attempt["status"] == "cancelled"
    assert attempt["failure"]["category"] == "cancelled"
    assert "run.cancel_requested" in [item["event_type"] for item in view["events"]]
