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

Artifacts are the one thing this store keeps *outside* its own tables.  A
registered artifact is copied into a content-addressed object store beside the
database, named for its digest, and the row records that.  The previous design
left the bytes in the attempt workspace, which made an artifact's lifetime the
same as a scratch directory's and made two attempts that produced identical
bytes store them twice.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from openroad_platform_contracts import (
    Artifact,
    AttemptStatus,
    Event,
    Metric,
    ResourceRequest,
    RuntimeStatus,
    StagedInput,
    TaskSpec,
    attempt_transition_allowed,
    is_terminal,
    run_transition_allowed,
)

from .digest import sha256

RUNTIME_SCHEMA_VERSION = 6

#: Where an artifact's bytes are.  Recorded per artifact rather than assumed,
#: because there have been two answers: artifacts registered before the object
#: store existed live in their attempt workspace, and are still readable there.
STORAGE_OBJECT = "object"
STORAGE_WORKSPACE = "workspace"

#: Named separately because one migration has to *rebuild* this table: SQLite
#: cannot drop a NOT NULL constraint in place, and an input's bytes may now come
#: from the object store instead of a host path.  Two copies of this shape would
#: eventually disagree, and the one that disagreed would be the migration.
_INPUTS_DDL = """
CREATE TABLE IF NOT EXISTS runtime_inputs (
    attempt_id TEXT NOT NULL REFERENCES runtime_attempts(attempt_id),
    destination TEXT NOT NULL,
    source TEXT,
    source_artifact_id TEXT,
    present INTEGER NOT NULL CHECK(present IN (0, 1)),
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    sha256 TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (attempt_id, destination)
);
"""

_DDL = """
CREATE TABLE IF NOT EXISTS runtime_schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runtime_runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    idempotency_key TEXT,
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
    resumable INTEGER NOT NULL DEFAULT 0,
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
    cpu_seconds REAL,
    peak_memory_bytes INTEGER,
    peak_processes INTEGER,
    UNIQUE(stage_run_id, attempt_number)
);
CREATE TABLE IF NOT EXISTS runtime_resource_reservations (
    attempt_id TEXT PRIMARY KEY REFERENCES runtime_attempts(attempt_id),
    cpu_cores INTEGER NOT NULL CHECK(cpu_cores > 0),
    memory_bytes INTEGER NOT NULL CHECK(memory_bytes > 0)
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
    storage TEXT NOT NULL,
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
""" + _INPUTS_DDL + """
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
CREATE UNIQUE INDEX IF NOT EXISTS runtime_runs_idempotency_key
    ON runtime_runs(idempotency_key) WHERE idempotency_key IS NOT NULL;
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _optional_json_object(raw: str | None) -> Mapping[str, Any] | None:
    """Decode a stored JSON object, or report its absence.

    Corrupt durable evidence is returned as an explicit storage failure.
    """
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {"category": "storage_corruption", "message": "invalid JSON", "retryable": False}
    return decoded if isinstance(decoded, dict) else {
        "category": "storage_corruption", "message": "not an object", "retryable": False,
    }


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _schema_version(raw: str) -> int:
    """Read the recorded schema version, refusing to guess at a broken one.

    Treating an unreadable version as "current" would run the wrong DDL against
    a real state root, so the failure is reported instead.
    """
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeStoreError(
            f"runtime schema version is not a number: {raw!r}"
        ) from exc


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
    #: The adapter's own report of why it failed, as it was written down.  It is
    #: carried here because the platform requires adapters to report it and then
    #: has to be able to show it: "the run failed" without "because the toolchain
    #: was not found" is a status, not a diagnosis.
    failure: Mapping[str, Any] | None = None


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

    def __init__(self, db_path: str | Path, objects_root: str | Path | None = None):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Beside the database by default, because that is where the composition
        # root puts it and the two must move together: a state root whose rows
        # outlive its objects is a state root full of artifacts that cannot be
        # read.  It is a parameter so that the composition root can say so
        # explicitly rather than leaving the layout to be inferred from here.
        self.objects_root = Path(
            objects_root if objects_root is not None
            else self.db_path.parent / "runtime-objects"
        ).expanduser()
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            str(self.db_path), check_same_thread=False, isolation_level=None
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = DELETE")
        self._initialise()

    # -- lifecycle ---------------------------------------------------------

    def resource_totals(self) -> tuple[int, int]:
        """Return currently reserved CPU cores and memory bytes."""
        row = self._connection.execute(
            "SELECT COALESCE(SUM(cpu_cores),0) cpu, COALESCE(SUM(memory_bytes),0) mem "
            "FROM runtime_resource_reservations"
        ).fetchone()
        return int(row["cpu"]), int(row["mem"])

    def release_resources(self, attempt_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM runtime_resource_reservations WHERE attempt_id = ?",
                (attempt_id,),
            )

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
            stored = _schema_version(row["value"])
            if stored == RUNTIME_SCHEMA_VERSION:
                return
            if stored > RUNTIME_SCHEMA_VERSION:
                raise RuntimeStoreError(
                    f"runtime schema {stored} was written by a newer build; "
                    f"this build speaks {RUNTIME_SCHEMA_VERSION}"
                )
            self._migrate(stored)

    def _migrate(self, from_version: int) -> None:
        """Bring an older state root forward, in place.

        Refusing to open a state root one version behind is how a research
        platform loses its run history to a routine upgrade, so older roots are
        migrated instead of rejected.  A *newer* root is still refused: this
        build cannot know what a later one wrote.

        Each step says what it is.  A step that is purely additive can re-run the
        idempotent DDL, which is why the first one looks like no work at all; a
        step that is not -- a new column on an existing table, a backfill, a
        split -- must be written out, because ``executescript`` would silently
        do nothing for it and the version would then claim work that never ran.
        """
        if from_version < 1:
            raise RuntimeStoreError(f"unsupported runtime schema {from_version!r}")
        if from_version < 6 and not self._has_column("runtime_runs", "idempotency_key"):
            self._connection.execute(
                "ALTER TABLE runtime_runs ADD COLUMN idempotency_key TEXT"
            )
        if from_version < 2:
            self._connection.executescript(_DDL)
        if from_version < 3:
            if not self._has_column("runtime_artifacts", "storage"):
                self._connection.execute(
                    "ALTER TABLE runtime_artifacts ADD COLUMN storage TEXT NOT "
                    f"NULL DEFAULT '{STORAGE_WORKSPACE}'"
                )
            if not self._has_column("runtime_inputs", "source_artifact_id"):
                self._connection.executescript(
                    "ALTER TABLE runtime_inputs RENAME TO runtime_inputs_v2;"
                    + _INPUTS_DDL
                    + "INSERT INTO runtime_inputs (attempt_id, destination, "
                    "source, source_artifact_id, present, size_bytes, sha256, "
                    "created_at) SELECT attempt_id, destination, source, NULL, "
                    "present, size_bytes, sha256, created_at "
                    "FROM runtime_inputs_v2;"
                    "DROP TABLE runtime_inputs_v2;"
                )
        if from_version < 4:
            self._connection.executescript(_DDL)
            for column, kind in (("cpu_seconds", "REAL"),
                                 ("peak_memory_bytes", "INTEGER"),
                                 ("peak_processes", "INTEGER")):
                if not self._has_column("runtime_attempts", column):
                    self._connection.execute(
                        f"ALTER TABLE runtime_attempts ADD COLUMN {column} {kind}"
                    )
        if from_version < 5 and not self._has_column("runtime_stage_runs", "resumable"):
            self._connection.execute(
                "ALTER TABLE runtime_stage_runs ADD COLUMN resumable INTEGER "
                "NOT NULL DEFAULT 0"
            )
        if from_version < 6:
            self._connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS runtime_runs_idempotency_key "
                "ON runtime_runs(idempotency_key) WHERE idempotency_key IS NOT NULL"
            )
        self._connection.execute(
            "UPDATE runtime_schema_meta SET value = ? WHERE key = 'schema_version'",
            (str(RUNTIME_SCHEMA_VERSION),),
        )

    def _has_column(self, table: str, column: str) -> bool:
        rows = self._connection.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row["name"] == column for row in rows)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> RuntimeStore:  # noqa: PYI034 - Python 3.9 support
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- submission --------------------------------------------------------

    def submit_run(
        self, task: TaskSpec, *, stage_key: str, plugin_version: str,
        idempotent: bool = False, idempotency_key: str | None = None,
        resumable: bool = False,
    ) -> RunRecord:
        """Create one run with a single stage.

        Multi-stage workflows are a later concern; a run that needs them adds
        stages through ``append_stage`` rather than a second code path here.
        """
        task.validate()
        if task.plugin_id is None:
            raise RuntimeStoreError("a run stage requires a plugin_id")
        if idempotency_key is not None and not idempotency_key:
            raise RuntimeStoreError("idempotency_key must not be empty")
        now = _now()
        run_id = _new_id("run")
        stage_run_id = _new_id("stage")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                # Lookup and creation share the write transaction so requests
                # on separate connections cannot both create the same task.
                if idempotency_key is not None:
                    row = self._connection.execute(
                        "SELECT * FROM runtime_runs WHERE idempotency_key = ?",
                        (idempotency_key,),
                    ).fetchone()
                    existing = self._run_from_row(row) if row else None
                else:
                    existing = self.find_run_by_task_id(task.task_id) if idempotent else None
                if existing is not None:
                    if existing.task_spec.to_dict() != task.to_dict():
                        raise RuntimeStoreError(
                            f"task_id {task.task_id!r} already exists with a different "
                            f"immutable TaskSpec"
                        )
                    stage = self.list_stages(existing.run_id)[0]
                    if stage.plugin_version != plugin_version:
                        raise RuntimeStoreError(
                            f"task_id {task.task_id!r} already exists at plugin version "
                            f"{stage.plugin_version!r}"
                        )
                    self._connection.execute("COMMIT")
                    return existing
                self._connection.execute(
                    "INSERT INTO runtime_runs (run_id, task_id, idempotency_key, status, "
                    "task_spec_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (run_id, task.task_id, idempotency_key,
                     RuntimeStatus.QUEUED.value,
                     json.dumps(task.to_dict(), sort_keys=True), now),
                )
                self._connection.execute(
                    "INSERT INTO runtime_stage_runs (stage_run_id, run_id, "
                    "stage_key, ordinal, plugin_id, plugin_version, status, "
                    "created_at, resumable) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (stage_run_id, run_id, stage_key, 0, task.plugin_id,
                     plugin_version, RuntimeStatus.QUEUED.value, now,
                     1 if resumable else 0),
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
                # A malformed or absent failure record is loaded as absent
                # rather than crashing the read model: the run is still real, and
                # a reader asking why it failed should not be told "the store is
                # unreadable".
                failure=_optional_json_object(r["failure_json"]),
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
        resources: ResourceRequest | None = None,
        capacity_cpu_cores: int | None = None,
        capacity_memory_bytes: int | None = None,
        platform_fraction: float = 0.60,
    ) -> Attempt | None:
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
                if (resources is not None and resources.declared
                        and capacity_cpu_cores is not None
                        and capacity_memory_bytes is not None):
                    cpu = resources.cpu_cores or 1
                    memory = resources.memory_bytes or 0
                    if memory <= 0:
                        raise RuntimeStoreError(
                            "memory_bytes is required for resource reservation"
                        )
                    totals = self._connection.execute(
                        "SELECT COALESCE(SUM(cpu_cores),0) cpu, COALESCE(SUM(memory_bytes),0) mem "
                        "FROM runtime_resource_reservations"
                    ).fetchone()
                    if (int(totals["cpu"]) + cpu > int(capacity_cpu_cores * platform_fraction)
                            or int(totals["mem"]) + memory > int(capacity_memory_bytes * platform_fraction)):
                        self._connection.execute("ROLLBACK")
                        return None
                    self._connection.execute(
                        "INSERT INTO runtime_resource_reservations "
                        "(attempt_id,cpu_cores,memory_bytes) VALUES (?,?,?)",
                        (attempt_id, cpu, memory),
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
        cpu_seconds: float | None = None, peak_memory_bytes: int | None = None,
        peak_processes: int | None = None,
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
                    "exit_code = ?, failure_json = ?, cpu_seconds = ?, "
                    "peak_memory_bytes = ?, peak_processes = ?, lease_expires_at = NULL "
                    "WHERE attempt_id = ?",
                    (status.value, _now(), exit_code,
                     json.dumps(dict(failure), sort_keys=True) if failure else None,
                     cpu_seconds, peak_memory_bytes, peak_processes, attempt_id),
                )
                self._connection.execute(
                    "DELETE FROM runtime_resource_reservations WHERE attempt_id = ?",
                    (attempt_id,),
                )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def schedule_retry(
        self, run_id: str, stage_run_id: str, *, reason: str,
    ) -> None:
        """Return a failed stage, and its run, to the queue for another attempt.

        Both move, because a worker's claim query requires both: a run left in
        ``retry_wait`` with a ``running`` stage is a retry nothing can ever pick
        up, and the caller would wait forever for something that cannot happen.

        The attempt that just failed stays FAILED.  It did fail, and that is
        evidence; the retry is a new attempt, not an erasure of the old one.
        """
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                stage = self._connection.execute(
                    "SELECT * FROM runtime_stage_runs WHERE stage_run_id = ?",
                    (stage_run_id,),
                ).fetchone()
                if stage is None:
                    raise RuntimeStoreError(f"unknown stage {stage_run_id!r}")
                stage_status = RuntimeStatus(stage["status"])
                if stage_status is not RuntimeStatus.RUNNING:
                    raise InvalidTransition(
                        f"only a running stage can be retried, not {stage_status.value}"
                    )
                row = self._connection.execute(
                    "SELECT status FROM runtime_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if row is None:
                    raise RuntimeStoreError(f"unknown run {run_id!r}")
                current = RuntimeStatus(row["status"])
                # The rule for which run transitions are legal lives in one
                # place, so a retry cannot invent a path the state machine
                # would refuse elsewhere.
                if not run_transition_allowed(current, RuntimeStatus.RETRY_WAIT):
                    raise InvalidTransition(
                        f"invalid run transition {current.value} -> retry_wait"
                    )
                self._connection.execute(
                    "UPDATE runtime_stage_runs SET status = ? WHERE stage_run_id = ?",
                    (RuntimeStatus.RETRY_WAIT.value, stage_run_id),
                )
                self._connection.execute(
                    "UPDATE runtime_runs SET status = ?, terminal_reason = ? "
                    "WHERE run_id = ?",
                    (RuntimeStatus.RETRY_WAIT.value, reason, run_id),
                )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def request_retry(self, run_id: str, *, requester: str, reason: str) -> dict[str, Any]:
        """Queue a completed retryable run without changing its immutable task."""
        if not reason.strip() or not requester.strip():
            raise RuntimeStoreError("retry reason and requester are required")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                run = self._connection.execute(
                    "SELECT * FROM runtime_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                if run is None:
                    raise RuntimeStoreError(f"unknown run {run_id!r}")
                task = TaskSpec.from_dict(json.loads(run["task_spec_json"]))
                stage = self._connection.execute(
                    "SELECT * FROM runtime_stage_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
                attempt = self._connection.execute(
                    "SELECT * FROM runtime_attempts WHERE stage_run_id = ? "
                    "ORDER BY attempt_number DESC LIMIT 1", (stage["stage_run_id"],)
                ).fetchone() if stage else None
                failure = _optional_json_object(attempt["failure_json"]) if attempt else None
                if run["status"] != RuntimeStatus.FAILED.value:
                    raise InvalidTransition("only failed runs may be retried")
                if attempt is None or not failure or not failure.get("retryable"):
                    raise RuntimeStoreError("the latest failure is not retryable")
                if attempt["attempt_number"] >= task.max_attempts:
                    raise RuntimeStoreError("retry budget exhausted")
                now = _now()
                self._connection.execute(
                    "UPDATE runtime_runs SET status = ?, terminal_reason = ? WHERE run_id = ?",
                    (RuntimeStatus.RETRY_WAIT.value, f"retry requested by {requester}: {reason}", run_id),
                )
                self._connection.execute(
                    "UPDATE runtime_stage_runs SET status = ? WHERE stage_run_id = ?",
                    (RuntimeStatus.RETRY_WAIT.value, stage["stage_run_id"]),
                )
                self._connection.execute(
                    "INSERT INTO runtime_events (event_id,run_id,stage_run_id,attempt_id,event_type,producer,payload_json,occurred_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (_new_id("event"), run_id, stage["stage_run_id"], attempt["attempt_id"],
                     "run.retry_requested", requester, json.dumps({"reason": reason}, sort_keys=True), now),
                )
                self._connection.execute("COMMIT")
                return self.describe_run(run_id)
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def _may_resume(self, run_id: str) -> bool:
        """Return a lease-lost run to the queue, if it has both the right and the room.

        Two conditions, and both are needed:

        * the capability says it can continue in a workspace it already has.  A
          capability that cannot would restart from nothing, which is not
          resuming and may be five hours of work thrown away by a guess.
        * the attempt budget has room.  A lost lease *is* an attempt -- the
          machine really did spend that time -- so it counts against
          ``max_attempts`` like any other.  Otherwise a host that loses its
          worker every time would loop for ever.
        """
        with self._lock:
            row = self._connection.execute(
                "SELECT s.stage_run_id, s.resumable, r.task_spec_json, "
                "(SELECT COUNT(*) FROM runtime_attempts "
                " WHERE stage_run_id = s.stage_run_id) AS made "
                "FROM runtime_runs r JOIN runtime_stage_runs s "
                "ON s.run_id = r.run_id WHERE r.run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None or not row["resumable"]:
            return False
        budget = TaskSpec.from_dict(json.loads(row["task_spec_json"])).max_attempts
        if int(row["made"]) >= budget:
            return False
        try:
            self.schedule_retry(
                run_id, row["stage_run_id"],
                reason="worker lost its lease; resuming in the same workspace",
            )
        except InvalidTransition:
            # Another path settled it first.  Whatever it decided stands.
            return False
        return True

    def runnable_runs(self, *, limit: int = 50) -> list[str]:
        """Run ids that have a stage a worker may claim.

        A single query rather than a scan of every run: a worker polls this on
        every cycle, and a poll that reads the whole history would get slower
        for the rest of the platform's life.
        """
        if not 1 <= limit <= 1000:
            raise RuntimeStoreError("limit must be between 1 and 1000")
        with self._lock:
            rows = self._connection.execute(
                "SELECT DISTINCT r.run_id, r.created_at FROM runtime_runs r "
                "JOIN runtime_stage_runs s ON s.run_id = r.run_id "
                "WHERE s.status IN ('queued', 'retry_wait') "
                "AND r.status IN ('queued', 'preparing', 'running', 'retry_wait') "
                "ORDER BY r.created_at LIMIT ?",
                (limit,),
            ).fetchall()
        return [row["run_id"] for row in rows]

    def abandoned_cancellations(self, *, limit: int = 50) -> list[str]:
        """Runs cancelled before any attempt could observe the request.

        A cancellation is recorded by moving the run and its non-terminal stages
        to ``cancel_requested``.  A run that was still queued has no running
        attempt to notice, so without this the request would sit there forever
        and the caller would never see the run reach a terminal state.
        """
        if not 1 <= limit <= 1000:
            raise RuntimeStoreError("limit must be between 1 and 1000")
        with self._lock:
            rows = self._connection.execute(
                "SELECT run_id FROM runtime_runs WHERE status = 'cancel_requested' "
                "ORDER BY created_at LIMIT ?",
                (limit,),
            ).fetchall()
            abandoned = []
            for row in rows:
                active = self._connection.execute(
                    "SELECT 1 FROM runtime_attempts a "
                    "JOIN runtime_stage_runs s ON s.stage_run_id = a.stage_run_id "
                    "WHERE s.run_id = ? AND a.status = 'running' LIMIT 1",
                    (row["run_id"],),
                ).fetchone()
                if active is None:
                    abandoned.append(row["run_id"])
        return abandoned

    def reclaim_expired_attempts(self, *, now: str | None = None) -> list[str]:
        """Mark attempts whose lease expired as lost, and settle their runs.

        Returns the attempt ids reclaimed.  LOST is terminal evidence: the work
        may or may not have happened, and the platform says so rather than
        assuming either.

        The run is settled too.  Marking only the attempt would leave the run in
        ``running`` with no attempt that can ever finish it -- worse than a wrong
        answer, because nobody would ever see it fail.
        """
        reference = now or _now()
        with self._lock:
            rows = self._connection.execute(
                "SELECT a.attempt_id, s.run_id FROM runtime_attempts a "
                "JOIN runtime_stage_runs s ON s.stage_run_id = a.stage_run_id "
                "WHERE a.status = 'running' AND a.lease_expires_at IS NOT NULL "
                "AND a.lease_expires_at < ?",
                (reference,),
            ).fetchall()
            ids = [r["attempt_id"] for r in rows]
            run_ids = sorted({r["run_id"] for r in rows})
            for attempt_id in ids:
                self._connection.execute(
                    "UPDATE runtime_attempts SET status = 'lost', ended_at = ?, "
                    "failure_json = ?, lease_expires_at = NULL WHERE attempt_id = ?",
                    (reference, json.dumps({
                        "category": "lease_expired",
                        "message": "worker stopped heartbeating; outcome unknown",
                        "retryable": True,
                    }, sort_keys=True), attempt_id),
                )
                self._connection.execute(
                    "DELETE FROM runtime_resource_reservations WHERE attempt_id = ?",
                    (attempt_id,),
                )
        for run_id in run_ids:
            if self._may_resume(run_id):
                # The work is not lost, it is unattended.  A capability that can
                # continue in the workspace it was given gets to: the run goes
                # back to the queue and the next attempt picks up where the
                # lease ran out.  The attempt itself stays LOST -- it did lose
                # its worker, and that is evidence.
                continue
            try:
                self.transition_run(run_id, RuntimeStatus.LOST,
                                    reason="lease_expired")
            except InvalidTransition:
                # A concurrent worker settled it first.  That is the state
                # machine working, not a failure.
                pass
        return ids

    # -- artifacts and metrics --------------------------------------------

    def register_artifacts(
        self, attempt_id: str, workspace: Path,
        declarations: Sequence[Mapping[str, Any]],
    ) -> list[str]:
        """Hash, record, and take custody of declared artifacts.

        The hash is computed here, from the bytes on disk.  A declared hash is
        a claim; this is the measurement.

        Custody is the second half and used to be missing.  An artifact's bytes
        stayed in the attempt workspace, so its lifetime was a scratch
        directory's, and two attempts that produced identical bytes kept two
        copies of them.  They are now copied into the object store under their
        own digest, where a repeated digest costs nothing and the record says
        which of the two places a reader should look.
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
                    self._store_object(path, artifact.sha256, artifact.size_bytes)
                    self._connection.execute(
                        "INSERT INTO runtime_artifacts (artifact_id, attempt_id, "
                        "kind, store_key, size_bytes, sha256, metadata_json, "
                        "created_at, storage) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (artifact.artifact_id, attempt_id, artifact.kind,
                         artifact.store_key, artifact.size_bytes, artifact.sha256,
                         json.dumps(artifact.metadata, sort_keys=True), _now(),
                         STORAGE_OBJECT),
                    )
                    created.append(artifact.artifact_id)
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return created

    # -- the object store --------------------------------------------------

    def object_path(self, digest: str) -> Path:
        """Where the bytes with this digest live.

        Two hex characters of fan-out, because a physical-design run produces
        thousands of reports and a single flat directory is a directory listing
        nobody wants to wait for.
        """
        return self.objects_root / digest[:2] / digest

    def _store_object(self, path: Path, digest: str, size_bytes: int) -> None:
        """Take a copy of ``path`` into the object store, once.

        The name is the digest, so an object that is already there is the same
        bytes by definition -- but its size is checked anyway, because a name
        that is a hash cannot honestly have two sizes, and discovering that it
        does is worth failing loudly over.

        Published by rename.  A reader that found a half-written object would
        find it under a valid name, which is the worst possible way to learn
        that a copy was interrupted.
        """
        target = self.object_path(digest)
        if target.is_file():
            existing = target.stat().st_size
            if existing != size_bytes or sha256(target) != digest:
                raise RuntimeStoreError(
                    f"object {digest} is {existing} bytes but the artifact is "
                    f"{size_bytes}: the object store is inconsistent"
                )
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f"{target.name}.partial-{uuid.uuid4().hex[:8]}")
        try:
            shutil.copyfile(path, partial)
            if partial.stat().st_size != size_bytes or sha256(partial) != digest:
                raise RuntimeStoreError("source changed while publishing object")
            partial.replace(target)
        finally:
            if partial.exists():
                partial.unlink()

    def get_artifact(self, artifact_id: str) -> Artifact:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM runtime_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        if row is None:
            raise RuntimeStoreError(f"no such artifact: {artifact_id!r}")
        return Artifact(
            artifact_id=row["artifact_id"], kind=row["kind"],
            store_key=row["store_key"], sha256=row["sha256"],
            size_bytes=row["size_bytes"],
            metadata=json.loads(row["metadata_json"]),
        )

    def artifact_path(self, artifact_id: str) -> Path:
        """Where an artifact's bytes are, according to its own record.

        The row says, because there have been two answers and only one of them
        is the current design.  A reader that guessed would be guessing about
        evidence.
        """
        with self._lock:
            row = self._connection.execute(
                "SELECT a.storage, a.store_key, a.sha256, t.workspace "
                "FROM runtime_artifacts a "
                "JOIN runtime_attempts t ON t.attempt_id = a.attempt_id "
                "WHERE a.artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        if row is None:
            raise RuntimeStoreError(f"no such artifact: {artifact_id!r}")
        if row["storage"] == STORAGE_OBJECT:
            return self.object_path(row["sha256"])
        if row["storage"] != STORAGE_WORKSPACE:
            raise RuntimeStoreError(
                f"artifact {artifact_id!r} records an unknown storage "
                f"{row['storage']!r}"
            )
        workspace = Path(str(row["workspace"])).resolve()
        path = (workspace / str(row["store_key"])).resolve()
        try:
            path.relative_to(workspace)
        except ValueError as exc:
            raise RuntimeStoreError(
                "registered artifact escapes the runtime workspace"
            ) from exc
        return path

    def materialize_artifact(self, artifact_id: str, destination: Path) -> Artifact:
        """Copy an artifact's bytes to ``destination`` and say what they are.

        Returns the record rather than the bytes so the caller can check what it
        just received against what the platform measured, which is the only way
        a check on this path means anything.
        """
        artifact = self.get_artifact(artifact_id)
        source = self.artifact_path(artifact_id)
        if not source.is_file():
            raise RuntimeStoreError(
                f"artifact {artifact_id!r} is recorded as {artifact.sha256} but "
                f"its bytes are missing from {source}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return artifact

    # -- inputs ------------------------------------------------------------

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

    # -- inputs ------------------------------------------------------------

    def record_inputs(
        self, attempt_id: str, staged_inputs: Sequence[StagedInput]
    ) -> None:
        """Record what the platform placed, and what it measured there.

        Written by the runtime after it has copied the bytes, so a row here
        describes a file that is really in the attempt workspace.  Nothing else
        in the platform may write it: a caller that could would be able to claim
        a digest for bytes it never read, which is exactly what digesting is for.
        """
        now = _now()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                for staged in staged_inputs:
                    staged.validate()
                    self._connection.execute(
                        "INSERT OR REPLACE INTO runtime_inputs (attempt_id, "
                        "destination, source, source_artifact_id, present, "
                        "size_bytes, sha256, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (attempt_id, staged.destination, staged.source,
                         staged.source_artifact_id,
                         1 if staged.present else 0, staged.size_bytes,
                         staged.sha256, now),
                    )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def list_inputs(self, attempt_id: str) -> list[StagedInput]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM runtime_inputs WHERE attempt_id = ? "
                "ORDER BY destination",
                (attempt_id,),
            ).fetchall()
        return [
            StagedInput(
                destination=r["destination"], source=r["source"],
                source_artifact_id=r["source_artifact_id"],
                present=bool(r["present"]), size_bytes=r["size_bytes"],
                sha256=r["sha256"],
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

    def _artifact_storages(self, attempt_id: str) -> dict[str, str]:
        """Which place each of an attempt's artifacts lives, in one query.

        A read model that issued one query per artifact would make the cost of
        describing a run grow with the number of reports the run produced, which
        for a physical-design flow is the wrong shape entirely.
        """
        with self._lock:
            rows = self._connection.execute(
                "SELECT artifact_id, storage FROM runtime_artifacts "
                "WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchall()
        return {row["artifact_id"]: row["storage"] for row in rows}

    def describe_run(self, run_id: str) -> dict[str, Any]:
        """A read model for apps.  Reads only; never mutates."""
        run = self.get_run(run_id)
        stages = []
        for stage in self.list_stages(run_id):
            attempts = []
            for attempt in self.list_attempts(stage.stage_run_id):
                storages = self._artifact_storages(attempt.attempt_id)
                attempts.append({
                    "attempt_id": attempt.attempt_id,
                    "attempt_number": attempt.attempt_number,
                    "status": attempt.status.value,
                    "workspace": attempt.workspace,
                    "worker_id": attempt.worker_id,
                    "failure": attempt.failure,
                    "resource_usage": self._attempt_usage(attempt.attempt_id),
                    "inputs": [
                        s.to_dict() for s in self.list_inputs(attempt.attempt_id)
                    ],
                    "artifacts": [
                        {
                            "artifact_id": a.artifact_id, "kind": a.kind,
                            "store_key": a.store_key, "sha256": a.sha256,
                            "size_bytes": a.size_bytes, "metadata": a.metadata,
                            "storage": storages.get(a.artifact_id, ""),
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

    def _attempt_usage(self, attempt_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT cpu_seconds, peak_memory_bytes, peak_processes FROM runtime_attempts WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
        if row is None or all(row[key] is None for key in ("cpu_seconds", "peak_memory_bytes", "peak_processes")):
            return None
        return {"cpu_seconds": row["cpu_seconds"], "peak_memory_bytes": row["peak_memory_bytes"], "peak_processes": row["peak_processes"]}
