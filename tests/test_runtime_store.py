from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from openroad_platform_contracts import Event, RuntimeStatus, TaskSpec
from openroad_platform_scheduler.runtime_store import RuntimeStore


def task(*, max_attempts: int = 2) -> TaskSpec:
    return TaskSpec(
        task_id="task-1",
        project_id="project-1",
        design_id="design-1",
        plugin_id="echo",
        max_attempts=max_attempts,
        timeout_seconds=30,
    )


def test_retry_creates_new_attempt_and_preserves_failed_evidence(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.db")
    run, stage = store.submit_plugin_run(task(), plugin_version="1.0.0")

    first = store.start_attempt(
        stage.stage_run_id, worker_id="worker-1",
        workspace=str(tmp_path / "attempt-1"), lease_seconds=30,
    )
    store.finish_attempt(
        first.attempt_id, RuntimeStatus.FAILED, exit_code=7,
        failure={"category": "tool_error", "message": "boom"},
    )
    assert store.get_stage(stage.stage_run_id).status is RuntimeStatus.RETRY_WAIT

    second = store.start_attempt(
        stage.stage_run_id, worker_id="worker-2",
        workspace=str(tmp_path / "attempt-2"), lease_seconds=30,
    )
    store.finish_attempt(second.attempt_id, RuntimeStatus.SUCCEEDED, exit_code=0)

    attempts = store.list_attempts(stage.stage_run_id)
    assert [item.attempt_number for item in attempts] == [1, 2]
    assert [item.status for item in attempts] == [
        RuntimeStatus.FAILED, RuntimeStatus.SUCCEEDED,
    ]
    assert attempts[0].failure == {"category": "tool_error", "message": "boom"}
    assert store.get_run(run.run_id).status is RuntimeStatus.SUCCEEDED


def test_expired_lease_marks_attempt_lost_and_retries_with_budget(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.db")
    run, stage = store.submit_plugin_run(task(max_attempts=2), plugin_version="1.0.0")
    now = datetime(2026, 8, 4, tzinfo=timezone.utc)
    first = store.start_attempt(
        stage.stage_run_id, worker_id="worker-1", workspace=str(tmp_path / "a1"),
        lease_seconds=5, now=now,
    )

    expired = store.expire_leases(now=now + timedelta(seconds=6))
    assert expired == (first.attempt_id,)
    assert store.get_attempt(first.attempt_id).status is RuntimeStatus.LOST
    assert store.get_stage(stage.stage_run_id).status is RuntimeStatus.RETRY_WAIT

    second = store.start_attempt(
        stage.stage_run_id, worker_id="worker-2", workspace=str(tmp_path / "a2"),
        lease_seconds=5, now=now + timedelta(seconds=7),
    )
    store.expire_leases(now=now + timedelta(seconds=13))
    assert store.get_attempt(second.attempt_id).status is RuntimeStatus.LOST
    assert store.get_run(run.run_id).status is RuntimeStatus.FAILED


def test_illegal_terminal_transition_is_atomic(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.db")
    run, stage = store.submit_plugin_run(task(max_attempts=1), plugin_version="1.0.0")
    attempt = store.start_attempt(
        stage.stage_run_id, worker_id="worker", workspace=str(tmp_path / "a"),
        lease_seconds=30,
    )
    store.finish_attempt(attempt.attempt_id, RuntimeStatus.SUCCEEDED, exit_code=0)
    event_count = len(store.events(run.run_id))

    with pytest.raises(ValueError, match="Invalid attempt transition"):
        store.finish_attempt(attempt.attempt_id, RuntimeStatus.FAILED, exit_code=1)

    assert store.get_attempt(attempt.attempt_id).status is RuntimeStatus.SUCCEEDED
    assert len(store.events(run.run_id)) == event_count


def test_cancel_request_is_recorded_before_worker_acknowledges(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.db")
    run, stage = store.submit_plugin_run(task(max_attempts=1), plugin_version="1.0.0")
    attempt = store.start_attempt(
        stage.stage_run_id, worker_id="worker", workspace=str(tmp_path / "a"),
        lease_seconds=30,
    )
    store.request_cancel(run.run_id)
    assert store.get_run(run.run_id).status is RuntimeStatus.CANCEL_REQUESTED
    assert store.get_stage(stage.stage_run_id).status is RuntimeStatus.CANCEL_REQUESTED

    store.finish_attempt(attempt.attempt_id, RuntimeStatus.CANCELLED, exit_code=-15)
    assert store.get_run(run.run_id).status is RuntimeStatus.CANCELLED


def test_cancel_queued_run_finishes_without_creating_attempt(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.db")
    run, stage = store.submit_plugin_run(task(), plugin_version="1.0.0")

    cancelled = store.request_cancel(run.run_id)

    assert cancelled.status is RuntimeStatus.CANCELLED
    assert cancelled.terminal_reason == "cancelled"
    assert store.get_stage(stage.stage_run_id).status is RuntimeStatus.CANCELLED
    assert store.list_attempts(stage.stage_run_id) == []
    assert [event["event_type"] for event in store.events(run.run_id)][-2:] == [
        "run.cancel_requested", "run.finished",
    ]
    assert all(event["schema_version"] == 1 for event in store.events(run.run_id))
    assert all(Event.from_dict(event) for event in store.events(run.run_id))


def test_runtime_store_rejects_unknown_database_schema_version(tmp_path):
    path = tmp_path / "runtime.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE runtime_schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO runtime_schema_meta VALUES ('schema_version', '999')"
        )

    with pytest.raises(RuntimeError, match="Unsupported runtime schema version"):
        RuntimeStore(path)


def test_adapter_event_requires_matching_run_stage_and_attempt(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.db")
    run, stage = store.submit_plugin_run(task(max_attempts=1), plugin_version="1.0.0")
    attempt = store.start_attempt(
        stage.stage_run_id, worker_id="worker", workspace=str(tmp_path / "a"),
        lease_seconds=30,
    )
    store.record_event(
        run.run_id, "tool.stage.started", {"tool_stage": "synth"},
        stage_run_id=stage.stage_run_id, attempt_id=attempt.attempt_id,
        producer="adapter:echo@1.0.0",
    )
    assert store.events(run.run_id)[-1]["payload"] == {"tool_stage": "synth"}
    with pytest.raises(ValueError, match="ownership"):
        store.record_event(
            "not-the-owner", "tool.stage.finished",
            {"tool_stage": "synth", "status": "failed"},
            stage_run_id=stage.stage_run_id, attempt_id=attempt.attempt_id,
            producer="adapter:echo@1.0.0",
        )


def test_runtime_store_rejects_unversioned_runtime_tables(tmp_path):
    path = tmp_path / "runtime.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE runtime_runs (run_id TEXT PRIMARY KEY)")

    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="unversioned database"):
        RuntimeStore(path)
    assert path.read_bytes() == before
