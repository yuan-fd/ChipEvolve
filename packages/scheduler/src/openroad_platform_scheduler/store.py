from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from openroad_platform_contracts import RunRequest, RunResult, RunStatus


TERMINAL = {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}


@dataclass(frozen=True)
class Job:
    id: str
    status: RunStatus
    request: RunRequest
    created_at: str
    updated_at: str
    claimed_by: str | None = None
    heartbeat_at: str | None = None
    result: dict | None = None
    error: str | None = None


class JobStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def submit(self, request: RunRequest) -> Job:
        request.validate()
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO jobs
                   (id, status, request_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (request.run_id, RunStatus.QUEUED.value,
                 json.dumps(request.to_dict(), ensure_ascii=False), now, now),
            )
            self._event(connection, request.run_id, "submitted", {})
        return self.get(request.run_id)

    def get(self, job_id: str) -> Job:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown job: {job_id}")
        return self._job(row)

    def list(self, *, limit: int = 50) -> list[Job]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._job(row) for row in rows]

    def claim_next(self, worker_id: str) -> Job | None:
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id FROM jobs WHERE status = ? ORDER BY created_at LIMIT 1",
                (RunStatus.QUEUED.value,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            updated = connection.execute(
                """UPDATE jobs SET status = ?, claimed_by = ?, heartbeat_at = ?,
                   updated_at = ? WHERE id = ? AND status = ?""",
                (RunStatus.PREPARING.value, worker_id, now, now, row["id"],
                 RunStatus.QUEUED.value),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return None
            self._event(connection, row["id"], "claimed", {"worker_id": worker_id})
            connection.commit()
        return self.get(row["id"])

    def mark_running(self, job_id: str) -> None:
        self._transition(job_id, {RunStatus.PREPARING}, RunStatus.RUNNING, "started")

    def heartbeat(self, job_id: str) -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET heartbeat_at = ?, updated_at = ? WHERE id = ?",
                (now, now, job_id),
            )

    def record_stage(self, job_id: str, payload: dict) -> None:
        with self._connect() as connection:
            self._event(connection, job_id, "stage_completed", payload)

    def request_cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.status == RunStatus.QUEUED:
            self._transition(job_id, {RunStatus.QUEUED}, RunStatus.CANCELLED, "cancelled")
        elif job.status in {RunStatus.PREPARING, RunStatus.RUNNING}:
            self._transition(
                job_id,
                {RunStatus.PREPARING, RunStatus.RUNNING},
                RunStatus.CANCEL_REQUESTED,
                "cancel_requested",
            )
        return self.get(job_id)

    def mark_cancelled(self, job_id: str) -> None:
        self._transition(
            job_id,
            {RunStatus.PREPARING, RunStatus.CANCEL_REQUESTED},
            RunStatus.CANCELLED,
            "cancelled",
        )

    def cancel_requested(self, job_id: str) -> bool:
        return self.get(job_id).status in {RunStatus.CANCEL_REQUESTED, RunStatus.CANCELLED}

    def complete(self, job_id: str, result: RunResult) -> None:
        if result.status not in TERMINAL:
            raise ValueError(f"Cannot complete a job with status {result.status.value}")
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """UPDATE jobs SET status = ?, result_json = ?, error = ?, updated_at = ?
                   WHERE id = ?""",
                (result.status.value, json.dumps(result.to_dict(), ensure_ascii=False),
                 result.error, now, job_id),
            )
            self._event(connection, job_id, "completed", {"status": result.status.value})

    def fail(self, job_id: str, error: str) -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                (RunStatus.FAILED.value, error, now, job_id),
            )
            self._event(connection, job_id, "failed", {"error": error})

    def events(self, job_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT kind, payload_json, created_at FROM job_events WHERE job_id = ? ORDER BY id",
                (job_id,),
            ).fetchall()
        return [{"kind": row["kind"], "payload": json.loads(row["payload_json"]),
                 "created_at": row["created_at"]} for row in rows]

    def _transition(
        self,
        job_id: str,
        allowed: set[RunStatus],
        target: RunStatus,
        event: str,
    ) -> None:
        now = _now()
        placeholders = ",".join("?" for _ in allowed)
        values = [target.value, now, job_id, *(item.value for item in allowed)]
        with self._connect() as connection:
            changed = connection.execute(
                f"UPDATE jobs SET status = ?, updated_at = ? WHERE id = ? "
                f"AND status IN ({placeholders})",
                values,
            )
            if changed.rowcount != 1:
                current = connection.execute(
                    "SELECT status FROM jobs WHERE id = ?", (job_id,)
                ).fetchone()
                value = current["status"] if current else "missing"
                raise ValueError(f"Invalid job transition {value} -> {target.value}")
            self._event(connection, job_id, event, {})

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT,
                    claimed_by TEXT,
                    heartbeat_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_status_created
                    ON jobs(status, created_at);
                CREATE TABLE IF NOT EXISTS job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _event(connection: sqlite3.Connection, job_id: str, kind: str, payload: dict) -> None:
        connection.execute(
            """INSERT INTO job_events (job_id, kind, payload_json, created_at)
               VALUES (?, ?, ?, ?)""",
            (job_id, kind, json.dumps(payload, ensure_ascii=False), _now()),
        )

    @staticmethod
    def _job(row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"],
            status=RunStatus(row["status"]),
            request=RunRequest.from_dict(json.loads(row["request_json"])),
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            error=row["error"],
            claimed_by=row["claimed_by"],
            heartbeat_at=row["heartbeat_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
