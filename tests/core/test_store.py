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
    ContractError,
    Metric,
    RuntimeStatus,
    StagedInput,
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


def test_a_newer_schema_version_is_refused(tmp_path: Path):
    """A root written by a later build is refused, not guessed at.

    This used to be "any other version", including an older one.  Refusing an
    older root costs the run history of every upgrade, so older roots are
    migrated instead -- and a newer one must still stop the build, because this
    build cannot know what a later one wrote.
    """
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
    with pytest.raises(RuntimeStoreError, match="written by a newer build"):
        RuntimeStore(path)


#: `runtime_artifacts` as the build before the object store wrote it: no
#: ``storage`` column, because there was only one place bytes could be.
ARTIFACTS_BEFORE_THE_OBJECT_STORE = """
CREATE TABLE runtime_artifacts (
    artifact_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL REFERENCES runtime_attempts(attempt_id),
    kind TEXT NOT NULL,
    store_key TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    sha256 TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(attempt_id, store_key)
)
"""


def make_root_look_older(path: Path, *, version: str = "1") -> None:
    """Put a state root back in the shape an earlier build wrote.

    Reshaping is the point: a migration that is only ever exercised against a
    root this build just made is not being tested at all.  Which tables exist
    depends on how far back the version goes -- ``runtime_inputs`` arrived at 2,
    ``storage`` at 3 -- so the fixture removes exactly what that build had not
    yet written.
    """
    import sqlite3

    statements = []
    if int(version) < 2:
        statements.append("DROP TABLE runtime_inputs;")
    elif int(version) < 3:
        # `runtime_inputs` existed at 2 but carried a NOT NULL source and knew
        # nothing about artifact references.
        statements.append(
            "ALTER TABLE runtime_inputs RENAME TO runtime_inputs_old;"
            "CREATE TABLE runtime_inputs ("
            " attempt_id TEXT NOT NULL REFERENCES runtime_attempts(attempt_id),"
            " destination TEXT NOT NULL,"
            " source TEXT NOT NULL,"
            " present INTEGER NOT NULL CHECK(present IN (0, 1)),"
            " size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),"
            " sha256 TEXT,"
            " created_at TEXT NOT NULL,"
            " PRIMARY KEY (attempt_id, destination));"
            "INSERT INTO runtime_inputs (attempt_id, destination, source,"
            " present, size_bytes, sha256, created_at)"
            " SELECT attempt_id, destination, source, present, size_bytes,"
            " sha256, created_at FROM runtime_inputs_old;"
            "DROP TABLE runtime_inputs_old;"
        )
    statements.extend([
        "ALTER TABLE runtime_artifacts RENAME TO runtime_artifacts_current;",
        ARTIFACTS_BEFORE_THE_OBJECT_STORE + ";",
        "INSERT INTO runtime_artifacts (artifact_id, attempt_id, kind, store_key,"
        " size_bytes, sha256, metadata_json, created_at)"
        " SELECT artifact_id, attempt_id, kind, store_key, size_bytes, sha256,"
        " metadata_json, created_at FROM runtime_artifacts_current;",
        "DROP TABLE runtime_artifacts_current;",
        f"UPDATE runtime_schema_meta SET value = '{version}'"
        " WHERE key = 'schema_version';",
    ])
    connection = sqlite3.connect(str(path))
    connection.executescript("".join(statements))
    connection.commit()
    connection.close()


def test_an_older_schema_is_migrated_rather_than_refused(tmp_path: Path):
    """An upgrade must not cost the runs already recorded."""
    from openroad_platform_runtime.store import RUNTIME_SCHEMA_VERSION

    path = tmp_path / "r.db"
    store = RuntimeStore(path)
    run = submit(store)
    store.close()
    make_root_look_older(path)

    migrated = RuntimeStore(path)
    try:
        assert migrated.get_run(run.run_id).task_id == run.task_id
        recorded = migrated._connection.execute(
            "SELECT value FROM runtime_schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert recorded["value"] == str(RUNTIME_SCHEMA_VERSION)
    finally:
        migrated.close()


def test_an_artifact_from_before_the_object_store_is_still_readable(tmp_path):
    """Old evidence must survive the upgrade, not merely not crash it.

    Artifacts registered by the previous build have their bytes in an attempt
    workspace and no object beside the database.  The migration has to say so
    rather than assume the new layout, because assuming is how a state root ends
    up full of artifacts that cannot be read.
    """
    import sqlite3

    path = tmp_path / "r.db"
    workspace = tmp_path / "old-workspace"
    workspace.mkdir()
    payload = b'{"area_um2": 1234.5}'
    (workspace / "report.json").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    store = RuntimeStore(path)
    run = submit(store)
    stage = store.list_stages(run.run_id)[0]
    store.close()

    make_root_look_older(path, version="2")

    # Now write the row the way that earlier build wrote it: no storage column
    # to say where the bytes are, because there was nowhere else they could be.
    connection = sqlite3.connect(str(path))
    connection.execute(
        "INSERT INTO runtime_attempts (attempt_id, stage_run_id, "
        "attempt_number, status, workspace, worker_id, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("att-old", stage.stage_run_id, 1, "succeeded", str(workspace),
         "w", "2026-01-01T00:00:00+00:00"),
    )
    connection.execute(
        "INSERT INTO runtime_artifacts (artifact_id, attempt_id, kind, store_key,"
        " size_bytes, sha256, metadata_json, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("art-old", "att-old", "report", "report.json", len(payload), digest,
         "{}", "2026-01-01T00:00:00+00:00"),
    )
    connection.commit()
    connection.close()

    migrated = RuntimeStore(path)
    try:
        assert migrated.artifact_path("art-old") == workspace / "report.json"
        stored = migrated._connection.execute(
            "SELECT storage FROM runtime_artifacts WHERE artifact_id = 'art-old'"
        ).fetchone()
        assert stored["storage"] == "workspace"
        view = migrated.describe_run(run.run_id)
        assert view["stages"][0]["attempts"][0]["artifacts"][0]["storage"] == (
            "workspace"
        )
    finally:
        migrated.close()


def test_the_migration_survives_being_interrupted_midway(tmp_path: Path):
    """DDL commits as it goes, so a half-migrated root must still open.

    The version is left behind deliberately: that is the state a crash between
    the schema change and the version update leaves, and reopening it must not
    die on a column that is already there.
    """
    path = tmp_path / "r.db"
    store = RuntimeStore(path)
    run = submit(store)
    store.close()

    import sqlite3

    connection = sqlite3.connect(str(path))
    connection.execute("DROP TABLE runtime_inputs")
    connection.execute(
        "UPDATE runtime_schema_meta SET value = '1' WHERE key = 'schema_version'"
    )
    connection.commit()
    connection.close()

    migrated = RuntimeStore(path)
    try:
        assert migrated.get_run(run.run_id).task_id == run.task_id
    finally:
        migrated.close()


def test_a_schema_version_that_is_not_a_number_is_refused(tmp_path: Path):
    import sqlite3

    path = tmp_path / "r.db"
    store = RuntimeStore(path)
    store.close()
    connection = sqlite3.connect(str(path))
    connection.execute(
        "UPDATE runtime_schema_meta SET value = 'two' WHERE key = 'schema_version'"
    )
    connection.commit()
    connection.close()
    with pytest.raises(RuntimeStoreError, match="not a number"):
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


def test_the_reason_an_attempt_failed_survives_to_the_reader(
    store: RuntimeStore, tmp_path: Path
):
    """The platform makes adapters report *why*, so it has to be able to show it.

    "The run failed" without "because the toolchain was not found" is a status,
    not a diagnosis, and it is the difference between an operator knowing what to
    fix and re-running the same broken thing.
    """
    run = submit(store)
    stage = store.list_stages(run.run_id)[0]
    attempt = store.start_attempt(stage.stage_run_id, worker_id="w1",
                                  workspace=tmp_path, lease_seconds=30)
    failure = {"category": "configuration_error",
               "message": "ORFS Makefile not found: /absent/flow/Makefile",
               "retryable": False}
    store.finish_attempt(attempt.attempt_id, AttemptStatus.FAILED,
                         exit_code=3, failure=failure)

    view = store.describe_run(run.run_id)
    assert view["stages"][0]["attempts"][0]["failure"] == failure

    # The read model is also the only place it can come from: the attempt is
    # loaded from the row, not from the value that was passed in.
    reloaded = store.list_attempts(stage.stage_run_id)[0]
    assert reloaded.failure == failure


def test_an_attempt_with_no_recorded_failure_says_nothing(
    store: RuntimeStore, tmp_path: Path
):
    """Absent is not the same as empty, and neither is an error."""
    run = submit(store)
    stage = store.list_stages(run.run_id)[0]
    attempt = store.start_attempt(stage.stage_run_id, worker_id="w1",
                                  workspace=tmp_path, lease_seconds=30)
    store.finish_attempt(attempt.attempt_id, AttemptStatus.SUCCEEDED, exit_code=0)
    view = store.describe_run(run.run_id)
    assert view["stages"][0]["attempts"][0]["failure"] is None


def test_scheduling_a_retry_moves_both_the_stage_and_the_run(
    store: RuntimeStore, tmp_path: Path
):
    """A run put back in the queue must actually be offered to a worker.

    ``runnable_runs`` requires *both* the stage and the run to be non-terminal,
    so a retry that moved only one of them would be accepted here and then never
    claimed -- a run waiting forever for something that cannot happen.
    """
    run = submit(store)
    stage = store.list_stages(run.run_id)[0]
    store.start_attempt(stage.stage_run_id, worker_id="w1",
                        workspace=tmp_path, lease_seconds=30)

    store.schedule_retry(run.run_id, stage.stage_run_id, reason="retrying: busy")

    assert store.get_run(run.run_id).status is RuntimeStatus.RETRY_WAIT
    assert store.list_stages(run.run_id)[0].status is RuntimeStatus.RETRY_WAIT
    assert run.run_id in store.runnable_runs()
    # And a new attempt can be claimed, numbered after the one that failed.
    again = store.start_attempt(stage.stage_run_id, worker_id="w1",
                                workspace=tmp_path, lease_seconds=30)
    assert again.attempt_number == 2


def test_a_retry_is_refused_when_the_stage_is_not_running(
    store: RuntimeStore, tmp_path: Path
):
    """Only a stage that just had an attempt can go back in the queue."""
    run = submit(store)
    stage = store.list_stages(run.run_id)[0]
    with pytest.raises(InvalidTransition):
        store.schedule_retry(run.run_id, stage.stage_run_id, reason="x")


# --------------------------------------------------------------------------
# staged inputs
# --------------------------------------------------------------------------

def test_inputs_are_recorded_against_the_attempt_that_read_them(store):
    run = submit(store)
    stage = store.list_stages(run.run_id)[0]
    attempt = store.start_attempt(
        stage.stage_run_id, worker_id="w", workspace="/tmp/ws",
        lease_seconds=30,
    )
    store.record_inputs(attempt.attempt_id, [
        StagedInput(destination="b.v", source="/data/b.v", present=True,
                    size_bytes=2, sha256="b" * 64),
        StagedInput(destination="a.v", source="/data/a.v", present=True,
                    size_bytes=1, sha256="a" * 64),
    ])
    # Ordered by destination, so a reader does not depend on insertion order.
    assert [i.destination for i in store.list_inputs(attempt.attempt_id)] == [
        "a.v", "b.v",
    ]


def test_an_absent_input_is_stored_without_a_digest(store):
    run = submit(store)
    stage = store.list_stages(run.run_id)[0]
    attempt = store.start_attempt(
        stage.stage_run_id, worker_id="w", workspace="/tmp/ws",
        lease_seconds=30,
    )
    store.record_inputs(attempt.attempt_id, [
        StagedInput(destination="maybe.sdc", source="/data/maybe.sdc",
                    present=False, size_bytes=0),
    ])
    recorded = store.list_inputs(attempt.attempt_id)[0]
    assert recorded.present is False
    assert recorded.sha256 is None


def test_the_store_refuses_an_input_record_it_did_not_measure(store):
    """A digest is the platform's measurement, so a malformed one is refused.

    The runtime is the only writer, and validation here is what keeps that
    claim true if a second caller ever appears.
    """
    run = submit(store)
    stage = store.list_stages(run.run_id)[0]
    attempt = store.start_attempt(
        stage.stage_run_id, worker_id="w", workspace="/tmp/ws",
        lease_seconds=30,
    )
    with pytest.raises(ContractError, match="needs a sha256"):
        store.record_inputs(attempt.attempt_id, [
            StagedInput(destination="a.v", source="/data/a.v", present=True,
                        size_bytes=1),
        ])
    assert store.list_inputs(attempt.attempt_id) == []


def test_identical_bytes_are_stored_once(store, tmp_path: Path):
    """Content addressing, which is the whole reason the store is named for the
    digest: a flow that emits the same report every run should cost one copy."""
    payload = b'{"area_um2": 1234.5}'
    digest = hashlib.sha256(payload).hexdigest()

    artifact_ids = []
    for number in (1, 2):
        run = submit(store, task_id=f"task-{number}")
        stage = store.list_stages(run.run_id)[0]
        workspace = tmp_path / f"ws{number}"
        workspace.mkdir()
        (workspace / "report.json").write_bytes(payload)
        attempt = store.start_attempt(
            stage.stage_run_id, worker_id="w", workspace=str(workspace),
            lease_seconds=30,
        )
        artifact_ids.extend(store.register_artifacts(
            attempt.attempt_id, workspace,
            [{"kind": "report", "store_key": "report.json"}],
        ))

    first, second = artifact_ids
    assert store.get_artifact(first).sha256 == digest
    assert store.get_artifact(second).sha256 == digest
    # One object, reached from both records.
    assert store.artifact_path(first) == store.artifact_path(second)
    assert store.object_path(digest).is_file()
    assert sorted(p.name for p in store.objects_root.glob("*/*")) == [digest]


def test_an_input_from_before_the_object_store_survives_the_upgrade(tmp_path):
    """The rebuilt `runtime_inputs` must keep its rows, with no artifact id.

    The rebuild is the one migration step that is not additive, so it is the one
    most worth a test: a copy that silently dropped rows would leave a run whose
    inputs vanished from its own record.
    """
    import sqlite3

    path = tmp_path / "r.db"
    store = RuntimeStore(path)
    run = submit(store)
    stage = store.list_stages(run.run_id)[0]
    attempt = store.start_attempt(
        stage.stage_run_id, worker_id="w", workspace=str(tmp_path / "ws"),
        lease_seconds=30,
    )
    store.record_inputs(attempt.attempt_id, [
        StagedInput(destination="a.v", source="/data/a.v", present=True,
                    size_bytes=3, sha256="a" * 64),
    ])
    store.close()

    make_root_look_older(path, version="2")

    migrated = RuntimeStore(path)
    try:
        recorded = migrated.list_inputs(attempt.attempt_id)
        assert len(recorded) == 1
        assert recorded[0].source == "/data/a.v"
        assert recorded[0].source_artifact_id is None
        assert recorded[0].sha256 == "a" * 64
    finally:
        migrated.close()
