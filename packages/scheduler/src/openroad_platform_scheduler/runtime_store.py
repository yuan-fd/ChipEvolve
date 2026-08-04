"""Transactional SQLite state for the generic Workflow Runtime."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from openroad_platform_contracts import RuntimeStatus, TaskSpec


RUNTIME_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RuntimeRun:
    run_id: str
    task_id: str
    status: RuntimeStatus
    task_spec: TaskSpec
    created_at: str
    started_at: str | None
    ended_at: str | None
    terminal_reason: str | None


@dataclass(frozen=True)
class RuntimeStageRun:
    stage_run_id: str
    run_id: str
    stage_key: str
    ordinal: int
    plugin_id: str
    plugin_version: str
    status: RuntimeStatus
    successful_attempt_id: str | None
    created_at: str
    started_at: str | None
    ended_at: str | None


@dataclass(frozen=True)
class RuntimeAttempt:
    attempt_id: str
    stage_run_id: str
    attempt_number: int
    status: RuntimeStatus
    workspace: str
    worker_id: str
    lease_expires_at: str | None
    heartbeat_at: str | None
    started_at: str
    ended_at: str | None
    exit_code: int | None
    failure: dict[str, Any] | None


class RuntimeStore:
    """The sole persistence writer for Run, StageRun, and Attempt state."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def submit_plugin_run(
        self,
        task: TaskSpec,
        *,
        plugin_version: str,
        stage_key: str = "plugin",
    ) -> tuple[RuntimeRun, RuntimeStageRun]:
        task.validate()
        if task.plugin_id is None:
            raise ValueError("submit_plugin_run requires TaskSpec.plugin_id")
        now = _now()
        run_id = uuid.uuid4().hex
        stage_run_id = uuid.uuid4().hex
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO runtime_runs
                   (run_id, task_id, status, task_spec_json, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (run_id, task.task_id, RuntimeStatus.QUEUED.value,
                 json.dumps(task.to_dict(), ensure_ascii=False), now),
            )
            connection.execute(
                """INSERT INTO runtime_stage_runs
                   (stage_run_id, run_id, stage_key, ordinal, plugin_id,
                    plugin_version, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (stage_run_id, run_id, stage_key, 1, task.plugin_id,
                 plugin_version, RuntimeStatus.QUEUED.value, now),
            )
            self._event(connection, run_id, "run.accepted", {"task_id": task.task_id})
            self._event(
                connection, run_id, "stage.ready",
                {"stage_key": stage_key, "plugin_id": task.plugin_id},
                stage_run_id=stage_run_id,
            )
        return self.get_run(run_id), self.get_stage(stage_run_id)

    def start_attempt(
        self,
        stage_run_id: str,
        *,
        worker_id: str,
        workspace: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> RuntimeAttempt:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        clock = _utc(now)
        timestamp = clock.isoformat()
        lease = (clock + timedelta(seconds=lease_seconds)).isoformat()
        attempt_id = uuid.uuid4().hex
        with self._transaction() as connection:
            stage = connection.execute(
                """SELECT s.*, r.status AS run_status, r.task_spec_json
                   FROM runtime_stage_runs s
                   JOIN runtime_runs r ON r.run_id = s.run_id
                   WHERE s.stage_run_id = ?""",
                (stage_run_id,),
            ).fetchone()
            if stage is None:
                raise KeyError(f"Unknown stage run: {stage_run_id}")
            current = RuntimeStatus(stage["status"])
            if current not in {RuntimeStatus.QUEUED, RuntimeStatus.RETRY_WAIT}:
                raise ValueError(f"Invalid stage transition {current.value} -> running")
            task = TaskSpec.from_dict(json.loads(stage["task_spec_json"]))
            count = connection.execute(
                "SELECT COUNT(*) FROM runtime_attempts WHERE stage_run_id = ?",
                (stage_run_id,),
            ).fetchone()[0]
            attempt_number = count + 1
            if attempt_number > task.max_attempts:
                raise ValueError("Attempt budget exhausted")
            connection.execute(
                """INSERT INTO runtime_attempts
                   (attempt_id, stage_run_id, attempt_number, status, workspace,
                    worker_id, lease_expires_at, heartbeat_at, started_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (attempt_id, stage_run_id, attempt_number, RuntimeStatus.RUNNING.value,
                 str(Path(workspace).expanduser().resolve()), worker_id, lease,
                 timestamp, timestamp),
            )
            connection.execute(
                """UPDATE runtime_stage_runs
                   SET status = ?, started_at = COALESCE(started_at, ?), ended_at = NULL
                   WHERE stage_run_id = ?""",
                (RuntimeStatus.RUNNING.value, timestamp, stage_run_id),
            )
            connection.execute(
                """UPDATE runtime_runs
                   SET status = ?, started_at = COALESCE(started_at, ?), ended_at = NULL
                   WHERE run_id = ? AND status IN (?, ?)""",
                (RuntimeStatus.RUNNING.value, timestamp, stage["run_id"],
                 RuntimeStatus.QUEUED.value, RuntimeStatus.RUNNING.value),
            )
            self._event(
                connection, stage["run_id"], "attempt.started",
                {"attempt_number": attempt_number, "worker_id": worker_id},
                stage_run_id=stage_run_id, attempt_id=attempt_id,
            )
        return self.get_attempt(attempt_id)

    def heartbeat(
        self,
        attempt_id: str,
        *,
        worker_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        clock = _utc(now)
        timestamp = clock.isoformat()
        lease = (clock + timedelta(seconds=lease_seconds)).isoformat()
        with self._transaction() as connection:
            changed = connection.execute(
                """UPDATE runtime_attempts
                   SET heartbeat_at = ?, lease_expires_at = ?
                   WHERE attempt_id = ? AND worker_id = ? AND status = ?""",
                (timestamp, lease, attempt_id, worker_id, RuntimeStatus.RUNNING.value),
            )
            if changed.rowcount != 1:
                raise ValueError("Heartbeat rejected for inactive attempt or wrong worker")

    def finish_attempt(
        self,
        attempt_id: str,
        status: RuntimeStatus,
        *,
        exit_code: int,
        failure: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        if status not in {
            RuntimeStatus.SUCCEEDED,
            RuntimeStatus.FAILED,
            RuntimeStatus.CANCELLED,
            RuntimeStatus.TIMED_OUT,
            RuntimeStatus.LOST,
        }:
            raise ValueError(f"Attempt cannot finish as {status.value}")
        with self._transaction() as connection:
            self._finish_attempt(
                connection, attempt_id, status, exit_code=exit_code,
                failure=failure, timestamp=_utc(now).isoformat(),
            )

    def request_cancel(self, run_id: str) -> RuntimeRun:
        now = _now()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT status FROM runtime_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown run: {run_id}")
            status = RuntimeStatus(row["status"])
            if status in {
                RuntimeStatus.SUCCEEDED, RuntimeStatus.FAILED,
                RuntimeStatus.CANCELLED, RuntimeStatus.TIMED_OUT,
            }:
                return self.get_run(run_id)
            active_attempts = connection.execute(
                """SELECT COUNT(*) FROM runtime_attempts a
                   JOIN runtime_stage_runs s ON s.stage_run_id = a.stage_run_id
                   WHERE s.run_id = ? AND a.status = ?""",
                (run_id, RuntimeStatus.RUNNING.value),
            ).fetchone()[0]
            self._event(
                connection, run_id, "run.cancel_requested", {"requested_at": now}
            )
            if active_attempts == 0:
                connection.execute(
                    """UPDATE runtime_runs
                       SET status = ?, ended_at = ?, terminal_reason = ?
                       WHERE run_id = ?""",
                    (RuntimeStatus.CANCELLED.value, now, "cancelled", run_id),
                )
                connection.execute(
                    """UPDATE runtime_stage_runs SET status = ?, ended_at = ?
                       WHERE run_id = ? AND status IN (?, ?)""",
                    (RuntimeStatus.CANCELLED.value, now, run_id,
                     RuntimeStatus.QUEUED.value, RuntimeStatus.RETRY_WAIT.value),
                )
                self._event(
                    connection, run_id, "run.finished",
                    {"status": RuntimeStatus.CANCELLED.value, "reason": "cancelled"},
                )
            else:
                connection.execute(
                    "UPDATE runtime_runs SET status = ? WHERE run_id = ?",
                    (RuntimeStatus.CANCEL_REQUESTED.value, run_id),
                )
                connection.execute(
                    """UPDATE runtime_stage_runs SET status = ?
                       WHERE run_id = ? AND status IN (?, ?, ?)""",
                    (RuntimeStatus.CANCEL_REQUESTED.value, run_id,
                     RuntimeStatus.QUEUED.value, RuntimeStatus.RUNNING.value,
                     RuntimeStatus.RETRY_WAIT.value),
                )
        return self.get_run(run_id)

    def expire_leases(self, *, now: datetime | None = None) -> tuple[str, ...]:
        timestamp = _utc(now).isoformat()
        with self._transaction() as connection:
            rows = connection.execute(
                """SELECT attempt_id FROM runtime_attempts
                   WHERE status = ? AND lease_expires_at < ?
                   ORDER BY started_at""",
                (RuntimeStatus.RUNNING.value, timestamp),
            ).fetchall()
            for row in rows:
                self._finish_attempt(
                    connection, row["attempt_id"], RuntimeStatus.LOST, exit_code=-1,
                    failure={"category": "worker_lost", "message": "Worker lease expired"},
                    timestamp=timestamp,
                )
        return tuple(row["attempt_id"] for row in rows)

    def register_artifact(
        self,
        attempt_id: str,
        *,
        kind: str,
        store_key: str,
        size_bytes: int,
        sha256: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        artifact_id = uuid.uuid4().hex
        with self._transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM runtime_attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone() is None:
                raise KeyError(f"Unknown attempt: {attempt_id}")
            connection.execute(
                """INSERT INTO runtime_artifacts
                   (artifact_id, attempt_id, kind, store_key, size_bytes, sha256,
                    metadata_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (artifact_id, attempt_id, kind, store_key, size_bytes, sha256,
                 json.dumps(metadata or {}, ensure_ascii=False), _now()),
            )
            stage = connection.execute(
                """SELECT s.run_id, a.stage_run_id FROM runtime_attempts a
                   JOIN runtime_stage_runs s ON s.stage_run_id = a.stage_run_id
                   WHERE a.attempt_id = ?""",
                (attempt_id,),
            ).fetchone()
            self._event(
                connection, stage["run_id"], "artifact.registered",
                {"artifact_id": artifact_id, "kind": kind, "store_key": store_key},
                stage_run_id=stage["stage_run_id"], attempt_id=attempt_id,
            )
        return artifact_id

    def register_artifacts(
        self,
        attempt_id: str,
        artifacts: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    ) -> tuple[str, ...]:
        artifact_ids = tuple(uuid.uuid4().hex for _ in artifacts)
        with self._transaction() as connection:
            owner = connection.execute(
                """SELECT s.run_id, a.stage_run_id FROM runtime_attempts a
                   JOIN runtime_stage_runs s ON s.stage_run_id = a.stage_run_id
                   WHERE a.attempt_id = ?""",
                (attempt_id,),
            ).fetchone()
            if owner is None:
                raise KeyError(f"Unknown attempt: {attempt_id}")
            for artifact_id, artifact in zip(artifact_ids, artifacts):
                connection.execute(
                    """INSERT INTO runtime_artifacts
                       (artifact_id, attempt_id, kind, store_key, size_bytes, sha256,
                        metadata_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (artifact_id, attempt_id, artifact["kind"], artifact["store_key"],
                     artifact["size_bytes"], artifact["sha256"],
                     json.dumps(artifact.get("metadata", {}), ensure_ascii=False), _now()),
                )
                self._event(
                    connection, owner["run_id"], "artifact.registered",
                    {"artifact_id": artifact_id, "kind": artifact["kind"],
                     "store_key": artifact["store_key"]},
                    stage_run_id=owner["stage_run_id"], attempt_id=attempt_id,
                )
        return artifact_ids

    def register_metrics(
        self,
        attempt_id: str,
        metrics: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    ) -> tuple[str, ...]:
        metric_ids = tuple(uuid.uuid4().hex for _ in metrics)
        with self._transaction() as connection:
            owner = connection.execute(
                """SELECT s.run_id, a.stage_run_id FROM runtime_attempts a
                   JOIN runtime_stage_runs s ON s.stage_run_id = a.stage_run_id
                   WHERE a.attempt_id = ?""",
                (attempt_id,),
            ).fetchone()
            if owner is None:
                raise KeyError(f"Unknown attempt: {attempt_id}")
            for metric_id, metric in zip(metric_ids, metrics):
                name = metric.get("name")
                if not isinstance(name, str) or not name:
                    raise ValueError("Metric name is required")
                connection.execute(
                    """INSERT INTO runtime_metrics
                       (metric_id, attempt_id, name, value_json, unit,
                        source_artifact_id, parser_id, parser_version,
                        context_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (metric_id, attempt_id, name,
                     json.dumps(metric.get("value"), ensure_ascii=False),
                     metric.get("unit"), metric.get("source_artifact_id"),
                     metric.get("parser_id"), metric.get("parser_version"),
                     json.dumps(metric.get("context", {}), ensure_ascii=False), _now()),
                )
                self._event(
                    connection, owner["run_id"], "metric.recorded",
                    {"metric_id": metric_id, "name": name},
                    stage_run_id=owner["stage_run_id"], attempt_id=attempt_id,
                )
        return metric_ids

    def get_run(self, run_id: str) -> RuntimeRun:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown run: {run_id}")
        return self._run(row)

    def get_stage(self, stage_run_id: str) -> RuntimeStageRun:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_stage_runs WHERE stage_run_id = ?",
                (stage_run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown stage run: {stage_run_id}")
        return self._stage(row)

    def get_attempt(self, attempt_id: str) -> RuntimeAttempt:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown attempt: {attempt_id}")
        return self._attempt(row)

    def list_runs(self, *, limit: int = 50) -> list[RuntimeRun]:
        limit = max(1, min(int(limit), 500))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runtime_runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._run(row) for row in rows]

    def list_stages(self, run_id: str) -> list[RuntimeStageRun]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM runtime_stage_runs WHERE run_id = ?
                   ORDER BY ordinal""",
                (run_id,),
            ).fetchall()
        return [self._stage(row) for row in rows]

    def list_attempts(self, stage_run_id: str) -> list[RuntimeAttempt]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM runtime_attempts WHERE stage_run_id = ?
                   ORDER BY attempt_number""",
                (stage_run_id,),
            ).fetchall()
        return [self._attempt(row) for row in rows]

    def events(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM runtime_events WHERE run_id = ?
                   ORDER BY sequence""",
                (run_id,),
            ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "schema_version": row["schema_version"],
                "run_id": row["run_id"],
                "event_type": row["event_type"],
                "stage_run_id": row["stage_run_id"],
                "attempt_id": row["attempt_id"],
                "producer": row["producer"],
                "payload": json.loads(row["payload_json"]),
                "occurred_at": row["occurred_at"],
            }
            for row in rows
        ]

    def artifacts(self, attempt_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM runtime_artifacts WHERE attempt_id = ?
                   ORDER BY created_at, artifact_id""",
                (attempt_id,),
            ).fetchall()
        return [
            {
                "artifact_id": row["artifact_id"], "kind": row["kind"],
                "store_key": row["store_key"], "size_bytes": row["size_bytes"],
                "sha256": row["sha256"], "metadata": json.loads(row["metadata_json"]),
            }
            for row in rows
        ]

    def metrics(self, attempt_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM runtime_metrics WHERE attempt_id = ?
                   ORDER BY created_at, metric_id""",
                (attempt_id,),
            ).fetchall()
        return [
            {
                "metric_id": row["metric_id"], "name": row["name"],
                "value": json.loads(row["value_json"]), "unit": row["unit"],
                "source_artifact_id": row["source_artifact_id"],
                "parser_id": row["parser_id"], "parser_version": row["parser_version"],
                "context": json.loads(row["context_json"]),
            }
            for row in rows
        ]

    def describe_run(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        stages = self.list_stages(run_id)
        return {
            "run": {
                "run_id": run.run_id, "task_id": run.task_id,
                "status": run.status.value, "task_spec": run.task_spec.to_dict(),
                "created_at": run.created_at, "started_at": run.started_at,
                "ended_at": run.ended_at, "terminal_reason": run.terminal_reason,
            },
            "stages": [
                {
                    "stage_run_id": stage.stage_run_id,
                    "stage_key": stage.stage_key,
                    "ordinal": stage.ordinal,
                    "plugin_id": stage.plugin_id,
                    "plugin_version": stage.plugin_version,
                    "status": stage.status.value,
                    "successful_attempt_id": stage.successful_attempt_id,
                    "attempts": [
                        {
                            "attempt_id": attempt.attempt_id,
                            "attempt_number": attempt.attempt_number,
                            "status": attempt.status.value,
                            "worker_id": attempt.worker_id,
                            "workspace": attempt.workspace,
                            "started_at": attempt.started_at,
                            "ended_at": attempt.ended_at,
                            "exit_code": attempt.exit_code,
                            "failure": attempt.failure,
                            "artifacts": self.artifacts(attempt.attempt_id),
                            "metrics": self.metrics(attempt.attempt_id),
                        }
                        for attempt in self.list_attempts(stage.stage_run_id)
                    ],
                }
                for stage in stages
            ],
            "events": self.events(run_id),
        }

    def _finish_attempt(
        self,
        connection: sqlite3.Connection,
        attempt_id: str,
        status: RuntimeStatus,
        *,
        exit_code: int,
        failure: dict[str, Any] | None,
        timestamp: str,
    ) -> None:
        row = connection.execute(
            """SELECT a.*, s.run_id, s.status AS stage_status, r.task_spec_json
               FROM runtime_attempts a
               JOIN runtime_stage_runs s ON s.stage_run_id = a.stage_run_id
               JOIN runtime_runs r ON r.run_id = s.run_id
               WHERE a.attempt_id = ?""",
            (attempt_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown attempt: {attempt_id}")
        if RuntimeStatus(row["status"]) is not RuntimeStatus.RUNNING:
            raise ValueError(
                f"Invalid attempt transition {row['status']} -> {status.value}"
            )
        connection.execute(
            """UPDATE runtime_attempts
               SET status = ?, ended_at = ?, exit_code = ?, failure_json = ?,
                   lease_expires_at = NULL
               WHERE attempt_id = ?""",
            (status.value, timestamp, exit_code,
             json.dumps(failure, ensure_ascii=False) if failure else None, attempt_id),
        )
        task = TaskSpec.from_dict(json.loads(row["task_spec_json"]))
        if status is RuntimeStatus.SUCCEEDED:
            stage_status = RuntimeStatus.SUCCEEDED
            connection.execute(
                """UPDATE runtime_stage_runs
                   SET status = ?, successful_attempt_id = ?, ended_at = ?
                   WHERE stage_run_id = ?""",
                (stage_status.value, attempt_id, timestamp, row["stage_run_id"]),
            )
        elif status is RuntimeStatus.CANCELLED:
            stage_status = RuntimeStatus.CANCELLED
            connection.execute(
                "UPDATE runtime_stage_runs SET status = ?, ended_at = ? WHERE stage_run_id = ?",
                (stage_status.value, timestamp, row["stage_run_id"]),
            )
        elif row["attempt_number"] < task.max_attempts:
            stage_status = RuntimeStatus.RETRY_WAIT
            connection.execute(
                "UPDATE runtime_stage_runs SET status = ?, ended_at = NULL WHERE stage_run_id = ?",
                (stage_status.value, row["stage_run_id"]),
            )
        else:
            stage_status = status
            connection.execute(
                "UPDATE runtime_stage_runs SET status = ?, ended_at = ? WHERE stage_run_id = ?",
                (stage_status.value, timestamp, row["stage_run_id"]),
            )
        self._event(
            connection, row["run_id"], "attempt.finished",
            {"status": status.value, "exit_code": exit_code,
             "will_retry": stage_status is RuntimeStatus.RETRY_WAIT},
            stage_run_id=row["stage_run_id"], attempt_id=attempt_id,
        )

        run_status: RuntimeStatus | None = None
        terminal_reason: str | None = None
        if stage_status is RuntimeStatus.SUCCEEDED:
            unfinished = connection.execute(
                """SELECT COUNT(*) FROM runtime_stage_runs
                   WHERE run_id = ? AND status != ?""",
                (row["run_id"], RuntimeStatus.SUCCEEDED.value),
            ).fetchone()[0]
            if unfinished == 0:
                run_status = RuntimeStatus.SUCCEEDED
        elif stage_status is RuntimeStatus.CANCELLED:
            run_status = RuntimeStatus.CANCELLED
            terminal_reason = "cancelled"
        elif stage_status is not RuntimeStatus.RETRY_WAIT:
            run_status = RuntimeStatus.FAILED
            terminal_reason = status.value
        if run_status is not None:
            connection.execute(
                """UPDATE runtime_runs
                   SET status = ?, ended_at = ?, terminal_reason = ? WHERE run_id = ?""",
                (run_status.value, timestamp, terminal_reason, row["run_id"]),
            )
            self._event(
                connection, row["run_id"], "run.finished",
                {"status": run_status.value, "reason": terminal_reason},
            )

    def _initialize(self) -> None:
        with self._connect() as connection:
            meta_exists = connection.execute(
                """SELECT 1 FROM sqlite_master
                   WHERE type = 'table' AND name = 'runtime_schema_meta'"""
            ).fetchone() is not None
            if not meta_exists:
                existing = connection.execute(
                    """SELECT name FROM sqlite_master
                       WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"""
                ).fetchone()
                if existing is not None:
                    raise RuntimeError(
                        "Refusing to initialize runtime tables in an unversioned "
                        "database; use a new database or an explicit migration"
                    )
                connection.execute(
                    """CREATE TABLE runtime_schema_meta (
                           key TEXT PRIMARY KEY,
                           value TEXT NOT NULL
                       )"""
                )
                connection.execute(
                    """INSERT INTO runtime_schema_meta (key, value)
                       VALUES ('schema_version', ?)""",
                    (str(RUNTIME_SCHEMA_VERSION),),
                )
            version = connection.execute(
                "SELECT value FROM runtime_schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            if version is None:
                raise RuntimeError("Runtime schema metadata has no schema_version")
            if version["value"] != str(RUNTIME_SCHEMA_VERSION):
                raise RuntimeError(
                    "Unsupported runtime schema version "
                    f"{version['value']!r}; expected {RUNTIME_SCHEMA_VERSION}"
                )
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
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
                    source_artifact_id TEXT REFERENCES runtime_artifacts(artifact_id),
                    parser_id TEXT,
                    parser_version TEXT,
                    context_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL REFERENCES runtime_runs(run_id),
                    stage_run_id TEXT,
                    attempt_id TEXT,
                    schema_version INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    producer TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_runtime_stage_status
                    ON runtime_stage_runs(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_runtime_attempt_lease
                    ON runtime_attempts(status, lease_expires_at);
                CREATE INDEX IF NOT EXISTS idx_runtime_event_run
                    ON runtime_events(run_id, sequence);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _transaction(self):
        return _Transaction(self._connect())

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        stage_run_id: str | None = None,
        attempt_id: str | None = None,
    ) -> None:
        connection.execute(
            """INSERT INTO runtime_events
               (event_id, run_id, stage_run_id, attempt_id, schema_version,
                event_type, producer, payload_json, occurred_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (uuid.uuid4().hex, run_id, stage_run_id, attempt_id,
             RUNTIME_SCHEMA_VERSION, event_type,
             "runtime", json.dumps(payload, ensure_ascii=False), _now()),
        )

    @staticmethod
    def _run(row: sqlite3.Row) -> RuntimeRun:
        return RuntimeRun(
            run_id=row["run_id"], task_id=row["task_id"],
            status=RuntimeStatus(row["status"]),
            task_spec=TaskSpec.from_dict(json.loads(row["task_spec_json"])),
            created_at=row["created_at"], started_at=row["started_at"],
            ended_at=row["ended_at"], terminal_reason=row["terminal_reason"],
        )

    @staticmethod
    def _stage(row: sqlite3.Row) -> RuntimeStageRun:
        return RuntimeStageRun(
            stage_run_id=row["stage_run_id"], run_id=row["run_id"],
            stage_key=row["stage_key"], ordinal=row["ordinal"],
            plugin_id=row["plugin_id"], plugin_version=row["plugin_version"],
            status=RuntimeStatus(row["status"]),
            successful_attempt_id=row["successful_attempt_id"],
            created_at=row["created_at"], started_at=row["started_at"],
            ended_at=row["ended_at"],
        )

    @staticmethod
    def _attempt(row: sqlite3.Row) -> RuntimeAttempt:
        return RuntimeAttempt(
            attempt_id=row["attempt_id"], stage_run_id=row["stage_run_id"],
            attempt_number=row["attempt_number"], status=RuntimeStatus(row["status"]),
            workspace=row["workspace"], worker_id=row["worker_id"],
            lease_expires_at=row["lease_expires_at"], heartbeat_at=row["heartbeat_at"],
            started_at=row["started_at"], ended_at=row["ended_at"],
            exit_code=row["exit_code"],
            failure=json.loads(row["failure_json"]) if row["failure_json"] else None,
        )


class _Transaction:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def __enter__(self) -> sqlite3.Connection:
        self.connection.execute("BEGIN IMMEDIATE")
        return self.connection

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            self.connection.commit()
        else:
            self.connection.rollback()
        self.connection.close()


def _utc(value: datetime | None = None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return result.astimezone(timezone.utc)


def _now() -> str:
    return _utc().isoformat()
