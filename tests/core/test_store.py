"""Runtime store invariants.

The store is the platform's memory.  A wrong answer here is worse than a crash:
it produces a plausible-looking result that nobody can audit.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import pytest

from openroad_platform_contracts import (
    AttemptStatus,
    Metric,
    RuntimeStatus,
    TaskSpec,
)
from openroad_platform_runtime.store import (
    InvalidTransition,
    RuntimeStore,
    RuntimeStoreError,
)


@pytest.fixture()
def store(tmp_path: Path) -> RuntimeStore:
    s = RuntimeStore(tmp_path / "runtime.db")
    yield s
    s.close()


def task(**overrides) -> TaskSpec:
    base = dict(
        task_id="task-1", project_id="p", design_id="d",
        plugin_id="some-capability",
    )
    base.update(overrides)
    return TaskSpec(**base)


def submit(store: RuntimeStore, **overrides):
    return store.submit_run(
        task(**overrides), stage_key="main", plugin_version="1.0.0",
    )


# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------

def test_reopening_the_same_database_is_fine(tmp_path: Path):
    path = tmp_path / "r.db"
    RuntimeStore(path).close()
    RuntimeStore(path).close()


def test_a_foreign_schema_version_is_refused(tmp_path: Path):
    import sqlite3

    path = tmp_path / "r.db"
    store = RuntimeStore(path)
    store.close()
    connection = sqlite3.connect(str(path))
    connection.execute(
        "UPDATE runtime_schema_meta SET value = '999' WHERE key = 'schema_version'"
    )
    connection.commit()
    connection.close()
    with pytest.raises(RuntimeStoreError, match="unsupported runtime schema"):
        RuntimeStore(path)


# --------------------------------------------------------------------------
# submission and lookup
# --------------------------------------------------------------------------

def test_submit_creates_one_queued_run_with_one_stage(store: RuntimeStore):
    run = submit(store)
    assert run.status is RuntimeStatus.QUEUED
    stages = store.list_stages(run.run_id)
    assert len(stages) == 1
    assert stages[0].stage_key == "main"
    assert stages[0].plugin_version == "1.0.0"


def test_find_by_task_id_returns_none_when_absent(store: RuntimeStore):
    assert store.find_run_by_task_id("nope") is None
    run = submit(store)
    assert store.find_run_by_task_id("task-1").run_id == run.run_id


def test_unknown_run_is_an_error_not_an_empty_result(store: RuntimeStore):
    with pytest.raises(RuntimeStoreError, match="unknown run"):
        store.get_run("run-nope")


# --------------------------------------------------------------------------
# the state machine
# --------------------------------------------------------------------------

def test_run_follows_the_declared_transition_table(store: RuntimeStore):
    run = submit(store)
    store.transition_run(run.run_id, RuntimeStatus.PREPARING)
    store.transition_run(run.run_id, RuntimeStatus.RUNNING)
    store.transition_run(run.run_id, RuntimeStatus.SUCCEEDED)
    assert store.get_run(run.run_id).status is RuntimeStatus.SUCCEEDED


def test_an_illegal_run_transition_is_refused(store: RuntimeStore):
    run = submit(store)
    with pytest.raises(InvalidTransition, match="queued -> succeeded"):
        store.transition_run(run.run_id, RuntimeStatus.SUCCEEDED)


def test_a_terminal_run_cannot_be_revived(store: RuntimeStore):
    """v1's hardest-won rule: LOST is evidence and must not be overwritten."""
    run = submit(store)
    store.transition_run(run.run_id, RuntimeStatus.PREPARING)
    store.transition_run(run.run_id, RuntimeStatus.RUNNING)
    store.transition_run(run.run_id, RuntimeStatus.LOST)
    with pytest.raises(InvalidTransition):
        store.transition_run(run.run_id, RuntimeStatus.RUNNING)


def test_transitioning_to_the_current_status_is_a_no_op(store: RuntimeStore):
    run = submit(store)
    store.transition_run(run.run_id, RuntimeStatus.QUEUED)
    assert store.get_run(run.run_id).status is RuntimeStatus.QUEUED


def test_cancel_records_intent_without_claiming_the_process_stopped(store: RuntimeStore):
    run = submit(store)
    store.request_cancel(run.run_id)
    assert store.get_run(run.run_id).status is RuntimeStatus.CANCEL_REQUESTED
    # Only the normal path may declare it actually cancelled.
    store.transition_run(run.run_id, RuntimeStatus.CANCELLED)


def test_cancelling_a_finished_run_changes_nothing(store: RuntimeStore):
    run = submit(store)
    store.transition_run(run.run_id, RuntimeStatus.PREPARING)
    store.transition_run(run.run_id, RuntimeStatus.RUNNING)
    store.transition_run(run.run_id, RuntimeStatus.SUCCEEDED)
    store.request_cancel(run.run_id)
    assert store.get_run(run.run_id).status is RuntimeStatus.SUCCEEDED


# --------------------------------------------------------------------------
# attempts and leases
# --------------------------------------------------------------------------

def test_starting_an_attempt_moves_run_and_stage_to_running(store: RuntimeStore):
    run = submit(store)
    stage = store.list_stages(run.run_id)[0]
    attempt = store.start_attempt(
        stage.stage_run_id, worker_id="w1",
        workspace=Path("/tmp/ws"), lease_seconds=30,
    )
    assert attempt.attempt_number == 1
    assert store.get_run(run.run_id).status is RuntimeStatus.RUNNING
    assert store.list_stages(run.run_id)[0].status is RuntimeStatus.RUNNING


def test_two_workers_cannot_claim_the_same_stage_twice(store: RuntimeStore):
    run = submit(store)
    stage = store.list_stages(run.run_id)[0]
    store.start_attempt(stage.stage_run_id, worker_id="w1",
                        workspace=Path("/tmp/a"), lease_seconds=30)
    with pytest.raises(InvalidTransition, match="running -> running"):
        store.start_attempt(stage.stage_run_id, worker_id="w2",
                            workspace=Path("/tmp/b"), lease_seconds=30)


def test_heartbeat_requires_the_lease_holder(store: RuntimeStore):
    run = submit(store)
    stage = store.list_stages(run.run_id)[0]
    attempt = store.start_attempt(stage.stage_run_id, worker_id="w1",
                                  workspace=Path("/tmp/ws"), lease_seconds=30)
    store.heartbeat(attempt.attempt_id, worker_id="w1", lease_seconds=30)
    with pytest.raises(RuntimeStoreError, match="leased by"):
        store.heartbeat(attempt.attempt_id, worker_id="w2", lease_seconds=30)


def test_expired_leases_become_lost_evidence(store: RuntimeStore):
    run = submit(store)
    stage = store.list_stages(run.run_id)[0]
    attempt = store.start_attempt(stage.stage_run_id, worker_id="w1",
                                  workspace=Path("/tmp/ws"), lease_seconds=30)
    reclaimed = store.reclaim_expired_attempts(now="2999-01-01T00:00:00+00:00")
    assert reclaimed == [attempt.attempt_id]
    attempts = store.list_attempts(stage.stage_run_id)
    assert attempts[0].status is AttemptStatus.LOST


def test_a_lost_attempt_cannot_be_finished_again(store: RuntimeStore):
    run = submit(store)
    stage = store.list_stages(run.run_id)[0]
    attempt = store.start_attempt(stage.stage_run_id, worker_id="w1",
                                  workspace=Path("/tmp/ws"), lease_seconds=30)
    store.reclaim_expired_attempts(now="2999-01-01T00:00:00+00:00")
    with pytest.raises(InvalidTransition, match="lost ->"):
        store.finish_attempt(attempt.attempt_id, AttemptStatus.SUCCEEDED)


def test_a_retry_gets_a_fresh_attempt_number(store: RuntimeStore):
    run = submit(store)
    stage = store.list_stages(run.run_id)[0]
    first = store.start_attempt(stage.stage_run_id, worker_id="w1",
                                workspace=Path("/tmp/1"), lease_seconds=30)
    store.finish_attempt(first.attempt_id, AttemptStatus.FAILED, exit_code=1)
    store.transition_run(run.run_id, RuntimeStatus.RETRY_WAIT)
    store.transition_run(run.run_id, RuntimeStatus.QUEUED)
    second = store.start_attempt(stage.stage_run_id, worker_id="w1",
                                 workspace=Path("/tmp/2"), lease_seconds=30)
    assert second.attempt_number == 2


# --------------------------------------------------------------------------
# artifacts -- the platform measures, it does not take a claim
# --------------------------------------------------------------------------

def test_artifact_hash_is_measured_from_disk(store: RuntimeStore, tmp_path: Path):
    run = submit(store)
    stage = store.list_stages(run.run_id)[0]
    attempt = store.start_attempt(stage.stage_run_id, worker_id="w1",
                                  workspace=tmp_path, lease_seconds=30)
    (tmp_path / "report.json").write_text('{"area": 1}', encoding="utf-8")

    ids = store.register_artifacts(attempt.attempt_id, tmp_path, [
        {"kind": "report", "store_key": "report.json",
         "sha256": "0" * 64},  # a lie the platform must ignore
    ])
    assert len(ids) == 1
    artifact = store.list_artifacts(attempt.attempt_id)[0]
    expected = hashlib.sha256(b'{"area": 1}').hexdigest()
    assert artifact.sha256 == expected


def test_a_missing_declared_artifact_is_refused(store: RuntimeStore, tmp_path: Path):
    run = submit(store)
    stage = store.list_stages(run.run_id)[0]
    attempt = store.start_attempt(stage.stage_run_id, worker_id="w1",
                                  workspace=tmp_path, lease_seconds=30)
    with pytest.raises(RuntimeStoreError, match="missing"):
        store.register_artifacts(attempt.attempt_id, tmp_path,
                                 [{"kind": "report", "store_key": "absent.json"}])


def test_an_artifact_may_not_escape_the_workspace(store: RuntimeStore, tmp_path: Path):
    run = submit(store)
    stage = store.list_stages(run.run_id)[0]
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    attempt = store.start_attempt(stage.stage_run_id, worker_id="w1",
                                  workspace=workspace, lease_seconds=30)
    with pytest.raises(RuntimeStoreError, match="escapes"):
        store.register_artifacts(attempt.attempt_id, workspace,
                                 [{"kind": "log", "store_key": "../outside.txt"}])


# --------------------------------------------------------------------------
# metrics and events
# --------------------------------------------------------------------------

def test_metrics_round_trip_with_their_source(store: RuntimeStore, tmp_path: Path):
    run = submit(store)
    stage = store.list_stages(run.run_id)[0]
    attempt = store.start_attempt(stage.stage_run_id, worker_id="w1",
                                  workspace=tmp_path, lease_seconds=30)
    (tmp_path / "m.json").write_text("{}", encoding="utf-8")
    artifact_id = store.register_artifacts(
        attempt.attempt_id, tmp_path, [{"kind": "report", "store_key": "m.json"}]
    )[0]
    store.register_metrics(attempt.attempt_id, [
        Metric(name="wns_ns", value=-0.12, unit="ns",
               source_artifact_id=artifact_id, parser_id="timing"),
    ])
    metrics = store.list_metrics(attempt.attempt_id)
    assert metrics[0].value == -0.12
    assert metrics[0].source_artifact_id == artifact_id


def test_events_are_append_only_and_ordered(store: RuntimeStore):
    run = submit(store)
    store.record_event(run.run_id, "stage.started",
                       {"stage": "anything"}, producer="runtime")
    store.record_event(run.run_id, "stage.finished",
                       {"stage": "anything", "status": "succeeded"},
                       producer="runtime")
    events = store.list_events(run.run_id)
    assert [e.event_type for e in events] == ["stage.started", "stage.finished"]


def test_describe_run_is_a_complete_read_model(store: RuntimeStore, tmp_path: Path):
    run = submit(store)
    stage = store.list_stages(run.run_id)[0]
    attempt = store.start_attempt(stage.stage_run_id, worker_id="w1",
                                  workspace=tmp_path, lease_seconds=30)
    (tmp_path / "a.log").write_text("hello", encoding="utf-8")
    store.register_artifacts(attempt.attempt_id, tmp_path,
                             [{"kind": "log", "store_key": "a.log"}])
    view = store.describe_run(run.run_id)
    assert view["run_id"] == run.run_id
    assert view["stages"][0]["attempts"][0]["artifacts"][0]["kind"] == "log"


# --------------------------------------------------------------------------
# concurrency
# --------------------------------------------------------------------------

def test_only_one_thread_wins_a_concurrent_claim(tmp_path: Path):
    """The lease is what stops two workers running the same experiment."""
    store = RuntimeStore(tmp_path / "runtime.db")
    try:
        run = submit(store)
        stage = store.list_stages(run.run_id)[0]
        outcomes: list[str] = []
        barrier = threading.Barrier(4)

        def claim(index: int) -> None:
            barrier.wait()
            try:
                store.start_attempt(stage.stage_run_id, worker_id=f"w{index}",
                                    workspace=Path(f"/tmp/{index}"),
                                    lease_seconds=30)
                outcomes.append("won")
            except InvalidTransition:
                outcomes.append("lost")

        threads = [threading.Thread(target=claim, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert outcomes.count("won") == 1
        assert outcomes.count("lost") == 3
    finally:
        store.close()
