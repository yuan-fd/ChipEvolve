"""Durable run state.

The Runtime is the only writer of a run's status.  A plugin, an app, or a model
may propose work; none of them may declare a result.  That is what makes a
stored QoR number worth anything.

Design notes carried over from v1 because they were right:

* A run has stages, a stage has attempts, an attempt has artifacts and metrics.
* Attempts are leased.  A worker that stops heartbeating loses its attempt to a
  visible, auditable state (``lost``) instead of leaving it ``running`` forever.
* Journal mode is DELETE, not WAL.  The state root may live on a shared
  filesystem where SQLite's WAL coordination is invalid.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .digest import sha256

from openroad_contracts import (
    Artifact,
    AttemptStatus,
    Event,
    Metric,
    PluginResult,
    RuntimeStatus,
    TaskSpec,
    attempt_transition_allowed,
    is_terminal,
    run_transition_allowed,
)

RUNTIME_SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS runtime_schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runtime_runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    status TEXT NOT NULL,
    task_spec_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    terminal_reason TEXT
);
CREATE TABLE IF NOT EXISTS runtime_stage_runs (
    stage_run_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runtime_runs(run_id),
    stage_key TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    plugin_id TEXT NOT NULL,
    plugin_version TEXT NOT NULL,
    status TEXT NOT NULL,
    successful_attempt_id TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    UNIQUE(run_id, stage_key),
    UNIQUE(run_id, ordinal)
);
CREATE TABLE IF NOT EXISTS runtime_attempts (
    attempt_id TEXT PRIMARY KEY,
    stage_run_id TEXT NOT NULL REFERENCES runtime_stage_runs(stage_run_id),
    attempt_number INTEGER NOT NULL CHECK(attempt_number > 0),
    status TEXT NOT NULL,
    workspace TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    lease_expires_at TEXT,
    heartbeat_at TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    exit_code INTEGER,
    failure_json TEXT,
    UNIQUE(stage_run_id, attempt_number)
);
CREATE TABLE IF NOT EXISTS runtime_artifacts (
    artifact_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL REFERENCES runtime_attempts(attempt_id),
    kind TEXT NOT NULL,
    store_key TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    sha256 TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(attempt_id, store_key)
);
CREATE TABLE IF NOT EXISTS runtime_metrics (
    metric_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL REFERENCES runtime_attempts(attempt_id),
    name TEXT NOT NULL,
    value_json TEXT NOT NULL,
    unit TEXT,
    source_artifact_id TEXT,
    parser_id TEXT,
    parser_version TEXT,
    context_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runtime_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    stage_run_id TEXT,
    attempt_id TEXT,
    event_type TEXT NOT NULL,
    producer TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runtime_events_run ON runtime_events(run_id, occurred_at);
CREATE INDEX IF NOT EXISTS runtime_metrics_attempt ON runtime_metrics(attempt_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


class RuntimeStoreError(RuntimeError):
    """Durable state refused an operation.  Never swallowed."""


class InvalidTransition(RuntimeStoreError):
    """A state change that the state machine does not allow."""


@dataclass(frozen=True)
class StageRun:
    stage_run_id: str
    run_id: str
    stage_key: str
    ordinal: int
    plugin_id: str
    plugin_version: str
    status: RuntimeStatus


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    stage_run_id: str
    attempt_number: int
    status: AttemptStatus
    workspace: str
    worker_id: str


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    task_id: str
    status: RuntimeStatus
    task_spec: TaskSpec
    created_at: str
    started_at: str | None
    ended_at: str | None
    terminal_reason: str | None


class RuntimeStore:
    """SQLite-backed durable run state.

    Thread-safe for the worker pool this platform runs: one connection guarded
    by a re-entrant lock, which is what v1 settled on after WAL proved invalid
    on a shared filesystem.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            str(self.db_path), check_same_thread=False, isolation_level=None
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = DELETE")
        self._initialise()

    # -- lifecycle ---------------------------------------------------------

    def _initialise(self) -> None:
        with self._lock:
            existing = self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='runtime_schema_meta'"
            ).fetchone()
            if existing is None:
                self._connection.executescript(_DDL)
                self._connection.execute(
                    "INSERT INTO runtime_schema_meta (key, value) VALUES (?, ?)",
                    ("schema_version", str(RUNTIME_SCHEMA_VERSION)),
                )
                return
            row = self._connection.execute(
                "SELECT value FROM runtime_schema_meta WHERE key='schema_version'"
            ).fetchone()
            if row is None:
                raise RuntimeStoreError("runtime schema has no schema_version")
            if row["value"] != str(RUNTIME_SCHEMA_VERSION):
                raise RuntimeStoreError(
                    f"unsupported runtime schema {row['value']!r}; "
                    f"this build speaks {RUNTIME_SCHEMA_VERSION}"
                )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "RuntimeStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- submission --------------------------------------------------------

    def submit_run(
        self, task: TaskSpec, *, stage_key: str, plugin_version: str
    ) -> RunRecord:
        """Create one run with a single stage.

        Multi-stage workflows are a later concern; a run that needs them adds
        stages through ``append_stage`` rather than a second code path here.
        """
        task.validate()
        if task.plugin_id is None:
            raise RuntimeStoreError("a run stage requires a plugin_id")
        now = _now()
        run_id = _new_id("run")
        stage_run_id = _new_id("stage")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    "INSERT INTO runtime_runs (run_id, task_id, status, "
                    "task_spec_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    (run_id, task.task_id, RuntimeStatus.QUEUED.value,
                     json.dumps(task.to_dict(), sort_keys=True), now),
                )
                self._connection.execute(
                    "INSERT INTO runtime_stage_runs (stage_run_id, run_id, "
                    "stage_key, ordinal, plugin_id, plugin_version, status, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (stage_run_id, run_id, stage_key, 0, task.plugin_id,
                     plugin_version, RuntimeStatus.QUEUED.value, now),
                )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return self.get_run(run_id)

    def find_run_by_task_id(self, task_id: str) -> RunRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM runtime_runs WHERE task_id = ? ORDER BY created_at LIMIT 1",
                (task_id,),
            ).fetchone()
        return self._run_from_row(row) if row else None

    def get_run(self, run_id: str) -> RunRecord:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM runtime_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise RuntimeStoreError(f"unknown run {run_id!r}")
        return self._run_from_row(row)

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            run_id=row["run_id"],
            task_id=row["task_id"],
            status=RuntimeStatus(row["status"]),
            task_spec=TaskSpec.from_dict(json.loads(row["task_spec_json"])),
            created_at=row["created_at"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            terminal_reason=row["terminal_reason"],
        )

    def list_stages(self, run_id: str) -> list[StageRun]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM runtime_stage_runs WHERE run_id = ? ORDER BY ordinal",
                (run_id,),
            ).fetchall()
        return [
            StageRun(
                stage_run_id=r["stage_run_id"], run_id=r["run_id"],
                stage_key=r["stage_key"], ordinal=r["ordinal"],
                plugin_id=r["plugin_id"], plugin_version=r["plugin_version"],
                status=RuntimeStatus(r["status"]),
            )
            for r in rows
        ]

    def list_attempts(self, stage_run_id: str) -> list[Attempt]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM runtime_attempts WHERE stage_run_id = ? "
                "ORDER BY attempt_number",
                (stage_run_id,),
            ).fetchall()
        return [
            Attempt(
                attempt_id=r["attempt_id"], stage_run_id=r["stage_run_id"],
                attempt_number=r["attempt_number"],
                status=AttemptStatus(r["status"]), workspace=r["workspace"],
                worker_id=r["worker_id"],
            )
            for r in rows
        ]

    # -- state machine -----------------------------------------------------

    def transition_run(
        self, run_id: str, target: RuntimeStatus, *, reason: str | None = None
    ) -> None:
        """Move a run's status, or refuse.

        An illegal transition is an error.  v1 learned this the hard way: a
        lease monitor could already have moved RUNNING to LOST, and a worker
        overwriting that would destroy the only evidence of what happened.
        """
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT status FROM runtime_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if row is None:
                    raise RuntimeStoreError(f"unknown run {run_id!r}")
                current = RuntimeStatus(row["status"])
                if current == target:
                    self._connection.execute("COMMIT")
                    return
                if not run_transition_allowed(current, target):
                    raise InvalidTransition(
                        f"invalid run transition {current.value} -> {target.value}"
                    )
                now = _now()
                started = ", started_at = COALESCE(started_at, ?)" if target \
                    is RuntimeStatus.RUNNING else ""
                params: list[Any] = [target.value]
                sql = f"UPDATE runtime_runs SET status = ?{started}"
                if started:
                    params.append(now)
                if is_terminal(target):
                    sql += ", ended_at = ?"
                    params.append(now)
                if reason is not None:
                    sql += ", terminal_reason = ?"
                    params.append(reason)
                sql += " WHERE run_id = ?"
                params.append(run_id)
                self._connection.execute(sql, params)
                self._connection.execute(
                    "UPDATE runtime_stage_runs SET status = ? WHERE run_id = ? "
                    "AND status NOT IN ('succeeded','failed','cancelled','timed_out','lost')",
                    (target.value, run_id),
                )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def request_cancel(self, run_id: str) -> None:
        """Record a cancellation request.

        This records intent; it does not claim the process stopped.  Only the
        normal lease path may move the run to ``cancelled``.
        """
        run = self.get_run(run_id)
        if is_terminal(run.status) or run.status is RuntimeStatus.CANCEL_REQUESTED:
            return
        self.transition_run(run_id, RuntimeStatus.CANCEL_REQUESTED,
                            reason="cancellation requested")

    def start_attempt(
        self, stage_run_id: str, *, worker_id: str, workspace: Path,
        lease_seconds: int,
    ) -> Attempt:
        """Claim the next attempt for a stage.  Transactional by construction."""
        if lease_seconds <= 0:
            raise RuntimeStoreError("lease_seconds must be positive")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                stage = self._connection.execute(
                    "SELECT * FROM runtime_stage_runs WHERE stage_run_id = ?",
                    (stage_run_id,),
                ).fetchone()
                if stage is None:
                    raise RuntimeStoreError(f"unknown stage {stage_run_id!r}")
                status = RuntimeStatus(stage["status"])
                if status not in {RuntimeStatus.QUEUED, RuntimeStatus.RETRY_WAIT}:
                    raise InvalidTransition(
                        f"invalid stage transition {status.value} -> running"
                    )
                number = self._connection.execute(
                    "SELECT COALESCE(MAX(attempt_number), 0) + 1 AS n "
                    "FROM runtime_attempts WHERE stage_run_id = ?",
                    (stage_run_id,),
                ).fetchone()["n"]
                attempt_id = _new_id("attempt")
                now = datetime.now(timezone.utc)
                expires = now + timedelta(seconds=lease_seconds)
                self._connection.execute(
                    "INSERT INTO runtime_attempts (attempt_id, stage_run_id, "
                    "attempt_number, status, workspace, worker_id, "
                    "lease_expires_at, heartbeat_at, started_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (attempt_id, stage_run_id, number, AttemptStatus.RUNNING.value,
                     str(workspace), worker_id, expires.isoformat(),
                     now.isoformat(), now.isoformat()),
                )
                self._connection.execute(
                    "UPDATE runtime_stage_runs SET status = ?, "
                    "started_at = COALESCE(started_at, ?) WHERE stage_run_id = ?",
                    (RuntimeStatus.RUNNING.value, now.isoformat(), stage_run_id),
                )
                self._connection.execute(
                    "UPDATE runtime_runs SET status = ?, "
                    "started_at = COALESCE(started_at, ?) WHERE run_id = ? "
                    "AND status IN ('queued','preparing','retry_wait')",
                    (RuntimeStatus.RUNNING.value, now.isoformat(), stage["run_id"]),
                )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return Attempt(
            attempt_id=attempt_id, stage_run_id=stage_run_id,
            attempt_number=number, status=AttemptStatus.RUNNING,
            workspace=str(workspace), worker_id=worker_id,
        )

    def heartbeat(self, attempt_id: str, *, worker_id: str, lease_seconds: int) -> None:
        """Extend a lease.  A mismatched worker is refused, not ignored."""
        now = datetime.now(timezone.utc)
        with self._lock:
            row = self._connection.execute(
                "SELECT worker_id, status FROM runtime_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise RuntimeStoreError(f"unknown attempt {attempt_id!r}")
            if row["worker_id"] != worker_id:
                raise RuntimeStoreError(
                    f"attempt {attempt_id!r} is leased by {row['worker_id']!r}"
                )
            if row["status"] != AttemptStatus.RUNNING.value:
                raise InvalidTransition(
                    f"cannot heartbeat a {row['status']} attempt"
                )
            self._connection.execute(
                "UPDATE runtime_attempts SET heartbeat_at = ?, lease_expires_at = ? "
                "WHERE attempt_id = ?",
                (now.isoformat(),
                 (now + timedelta(seconds=lease_seconds)).isoformat(), attempt_id),
            )

    def finish_attempt(
        self, attempt_id: str, status: AttemptStatus, *,
        exit_code: int | None = None, failure: Mapping[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT * FROM runtime_attempts WHERE attempt_id = ?",
                    (attempt_id,),
                ).fetchone()
                if row is None:
                    raise RuntimeStoreError(f"unknown attempt {attempt_id!r}")
                current = AttemptStatus(row["status"])
                if not attempt_transition_allowed(current, status):
                    raise InvalidTransition(
                        f"invalid attempt transition {current.value} -> {status.value}"
                    )
                self._connection.execute(
                    "UPDATE runtime_attempts SET status = ?, ended_at = ?, "
                    "exit_code = ?, failure_json = ?, lease_expires_at = NULL "
                    "WHERE attempt_id = ?",
                    (status.value, _now(), exit_code,
                     json.dumps(dict(failure), sort_keys=True) if failure else None,
                     attempt_id),
                )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def reclaim_expired_attempts(self, *, now: str | None = None) -> list[str]:
        """Mark attempts whose lease expired as lost.

        Returns the attempt ids reclaimed.  LOST is terminal evidence: the work
        may or may not have happened, and the platform says so rather than
        assuming either.
        """
        reference = now or _now()
        with self._lock:
            rows = self._connection.execute(
                "SELECT attempt_id FROM runtime_attempts WHERE status = 'running' "
                "AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
                (reference,),
            ).fetchall()
            ids = [r["attempt_id"] for r in rows]
            for attempt_id in ids:
                self._connection.execute(
                    "UPDATE runtime_attempts SET status = 'lost', ended_at = ?, "
                    "failure_json = ? WHERE attempt_id = ?",
                    (reference, json.dumps({
                        "category": "lease_expired",
                        "message": "worker stopped heartbeating; outcome unknown",
                        "retryable": True,
                    }, sort_keys=True), attempt_id),
                )
        return ids

    # -- artifacts and metrics --------------------------------------------

    def register_artifacts(
        self, attempt_id: str, workspace: Path,
        declarations: Sequence[Mapping[str, Any]],
    ) -> list[str]:
        """Hash and record declared artifacts.

        The hash is computed here, from the bytes on disk.  A declared hash is
        a claim; this is the measurement.
        """
        workspace = Path(workspace).resolve()
        created: list[str] = []
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                for declaration in declarations:
                    store_key = str(declaration["store_key"])
                    path = (workspace / store_key).resolve()
                    try:
                        path.relative_to(workspace)
                    except ValueError as exc:
                        raise RuntimeStoreError(
                            f"artifact escapes the attempt workspace: {store_key!r}"
                        ) from exc
                    if not path.is_file():
                        raise RuntimeStoreError(
                            f"declared artifact is missing: {store_key!r}"
                        )
                    artifact = Artifact(
                        artifact_id=_new_id("art"),
                        kind=str(declaration["kind"]),
                        store_key=store_key,
                        sha256=sha256(path),
                        size_bytes=path.stat().st_size,
                        media_type=declaration.get("media_type"),
                        metadata=dict(declaration.get("metadata") or {}),
                    )
                    artifact.validate()
                    self._connection.execute(
                        "INSERT INTO runtime_artifacts (artifact_id, attempt_id, "
                        "kind, store_key, size_bytes, sha256, metadata_json, "
                        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (artifact.artifact_id, attempt_id, artifact.kind,
                         artifact.store_key, artifact.size_bytes, artifact.sha256,
                         json.dumps(artifact.metadata, sort_keys=True), _now()),
                    )
                    created.append(artifact.artifact_id)
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return created

    def register_metrics(self, attempt_id: str, metrics: Iterable[Metric]) -> list[str]:
        created: list[str] = []
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                for metric in metrics:
                    metric.validate()
                    metric_id = _new_id("metric")
                    self._connection.execute(
                        "INSERT INTO runtime_metrics (metric_id, attempt_id, name, "
                        "value_json, unit, source_artifact_id, parser_id, "
                        "parser_version, context_json, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (metric_id, attempt_id, metric.name,
                         json.dumps(metric.value), metric.unit,
                         metric.source_artifact_id, metric.parser_id,
                         metric.parser_version,
                         json.dumps(metric.context, sort_keys=True), _now()),
                    )
                    created.append(metric_id)
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return created

    def list_metrics(self, attempt_id: str) -> list[Metric]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM runtime_metrics WHERE attempt_id = ? ORDER BY rowid",
                (attempt_id,),
            ).fetchall()
        return [
            Metric(
                name=r["name"], value=json.loads(r["value_json"]), unit=r["unit"],
                source_artifact_id=r["source_artifact_id"],
                parser_id=r["parser_id"], parser_version=r["parser_version"],
                context=json.loads(r["context_json"]),
            )
            for r in rows
        ]

    def list_artifacts(self, attempt_id: str) -> list[Artifact]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM runtime_artifacts WHERE attempt_id = ? ORDER BY rowid",
                (attempt_id,),
            ).fetchall()
        return [
            Artifact(
                artifact_id=r["artifact_id"], kind=r["kind"],
                store_key=r["store_key"], sha256=r["sha256"],
                size_bytes=r["size_bytes"],
                metadata=json.loads(r["metadata_json"]),
            )
            for r in rows
        ]

    # -- events ------------------------------------------------------------

    def record_event(
        self, run_id: str, event_type: str, payload: Mapping[str, Any], *,
        producer: str, stage_run_id: str | None = None,
        attempt_id: str | None = None,
    ) -> Event:
        event = Event(
            event_id=_new_id("evt"), run_id=run_id, event_type=event_type,
            occurred_at=_now(), producer=producer,
            payload=dict(payload), stage_run_id=stage_run_id,
            attempt_id=attempt_id,
        )
        with self._lock:
            self._connection.execute(
                "INSERT INTO runtime_events (event_id, run_id, stage_run_id, "
                "attempt_id, event_type, producer, payload_json, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (event.event_id, run_id, stage_run_id, attempt_id, event_type,
                 producer, json.dumps(event.payload, sort_keys=True),
                 event.occurred_at),
            )
        return event

    def list_events(self, run_id: str) -> list[Event]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM runtime_events WHERE run_id = ? ORDER BY occurred_at, rowid",
                (run_id,),
            ).fetchall()
        return [
            Event(
                event_id=r["event_id"], run_id=r["run_id"],
                event_type=r["event_type"], occurred_at=r["occurred_at"],
                producer=r["producer"], payload=json.loads(r["payload_json"]),
                stage_run_id=r["stage_run_id"], attempt_id=r["attempt_id"],
            )
            for r in rows
        ]

    # -- projection --------------------------------------------------------

    def describe_run(self, run_id: str) -> dict[str, Any]:
        """A read model for apps.  Reads only; never mutates."""
        run = self.get_run(run_id)
        stages = []
        for stage in self.list_stages(run_id):
            attempts = []
            for attempt in self.list_attempts(stage.stage_run_id):
                attempts.append({
                    "attempt_id": attempt.attempt_id,
                    "attempt_number": attempt.attempt_number,
                    "status": attempt.status.value,
                    "workspace": attempt.workspace,
                    "worker_id": attempt.worker_id,
                    "artifacts": [
                        {
                            "artifact_id": a.artifact_id, "kind": a.kind,
                            "store_key": a.store_key, "sha256": a.sha256,
                            "size_bytes": a.size_bytes, "metadata": a.metadata,
                        }
                        for a in self.list_artifacts(attempt.attempt_id)
                    ],
                    "metrics": [
                        m.to_dict() for m in self.list_metrics(attempt.attempt_id)
                    ],
                })
            stages.append({
                "stage_run_id": stage.stage_run_id,
                "stage_key": stage.stage_key,
                "ordinal": stage.ordinal,
                "plugin_id": stage.plugin_id,
                "plugin_version": stage.plugin_version,
                "status": stage.status.value,
                "attempts": attempts,
            })
        return {
            "run_id": run.run_id,
            "task_id": run.task_id,
            "status": run.status.value,
            "created_at": run.created_at,
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "terminal_reason": run.terminal_reason,
            "stages": stages,
        }
