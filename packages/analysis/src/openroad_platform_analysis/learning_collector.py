"""Tenant-isolated, read-only collection of terminal Runtime evidence."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openroad_platform_contracts import LearningContext, LearningObservation

from .learning_data import RuntimeEvidenceExporter


TENANT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class CollectionReceipt:
    collection_id: str
    tenant_id: str
    project_id: str
    run_id: str
    attempt_id: str
    context_fingerprint: str
    parser_version: str
    status: str
    observation_id: str | None
    reason: str | None


class TenantLearningStore:
    """Observed-only store with tenant/project on every access path."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS tenant_observations_v1 (
                    tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    observation_id TEXT NOT NULL, payload_json TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, opt_in_shared INTEGER NOT NULL DEFAULT 0,
                    tombstoned INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                    PRIMARY KEY(tenant_id, project_id, observation_id),
                    UNIQUE(tenant_id, project_id, fingerprint),
                    CHECK(opt_in_shared IN (0,1)), CHECK(tombstoned IN (0,1))
                );
                CREATE TABLE IF NOT EXISTS learning_collection_v1 (
                    collection_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL, run_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL, context_fingerprint TEXT NOT NULL,
                    parser_version TEXT NOT NULL, status TEXT NOT NULL,
                    observation_id TEXT, reason TEXT, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(tenant_id, project_id, run_id, attempt_id,
                           context_fingerprint, parser_version)
                );
                CREATE TABLE IF NOT EXISTS learning_rejections_v1 (
                    rejection_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL, run_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL, context_fingerprint TEXT NOT NULL,
                    run_status TEXT NOT NULL, reason TEXT NOT NULL,
                    metrics_snapshot TEXT, created_at TEXT NOT NULL,
                    UNIQUE(tenant_id, project_id, run_id, attempt_id,
                           context_fingerprint)
                );
            """)

    def admit(self, tenant_id: str, project_id: str,
              observation: LearningObservation) -> str:
        self._scope(tenant_id, project_id)
        observation.validate()
        payload = observation.to_dict()
        fingerprint = observation.fingerprint
        with self._connect() as connection:
            try:
                connection.execute("""INSERT INTO tenant_observations_v1
                    VALUES (?, ?, ?, ?, ?, 0, 0, datetime('now'))""",
                    (tenant_id, project_id, observation.observation_id,
                     json.dumps(payload, ensure_ascii=False), fingerprint))
            except sqlite3.IntegrityError:
                row = connection.execute("""SELECT fingerprint FROM tenant_observations_v1
                    WHERE tenant_id = ? AND project_id = ? AND observation_id = ?""",
                    (tenant_id, project_id, observation.observation_id)).fetchone()
                if row is None or row[0] != fingerprint:
                    raise ValueError("Tenant observation identity conflict")
        return observation.observation_id

    def list(self, tenant_id: str, project_id: str) -> list[LearningObservation]:
        self._scope(tenant_id, project_id)
        with self._connect() as connection:
            rows = connection.execute("""SELECT payload_json FROM tenant_observations_v1
                WHERE tenant_id = ? AND project_id = ? AND tombstoned = 0
                ORDER BY created_at, observation_id""", (tenant_id, project_id)).fetchall()
        return [LearningObservation.from_dict(json.loads(row[0])) for row in rows]

    def rejections(self, tenant_id: str, project_id: str) -> list[dict[str, Any]]:
        """Audit trail of rejected runs (never enters the learning observations)."""
        self._scope(tenant_id, project_id)
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("""SELECT rejection_id, run_id, attempt_id,
                context_fingerprint, run_status, reason, metrics_snapshot, created_at
                FROM learning_rejections_v1
                WHERE tenant_id = ? AND project_id = ?
                ORDER BY created_at, rejection_id""", (tenant_id, project_id)).fetchall()
        return [dict(row) for row in rows]

    def set_shared_opt_in(self, tenant_id: str, project_id: str,
                          observation_id: str, enabled: bool) -> None:
        self._scope(tenant_id, project_id)
        with self._connect() as connection:
            cursor = connection.execute("""UPDATE tenant_observations_v1
                SET opt_in_shared = ? WHERE tenant_id = ? AND project_id = ?
                AND observation_id = ? AND tombstoned = 0""",
                (int(enabled), tenant_id, project_id, observation_id))
            if cursor.rowcount != 1:
                raise KeyError("Unknown tenant observation")

    def shared(self) -> list[LearningObservation]:
        with self._connect() as connection:
            rows = connection.execute("""SELECT payload_json FROM tenant_observations_v1
                WHERE opt_in_shared = 1 AND tombstoned = 0 ORDER BY created_at""").fetchall()
        return [LearningObservation.from_dict(json.loads(row[0])) for row in rows]

    @staticmethod
    def _scope(tenant_id: str, project_id: str) -> None:
        if not TENANT.fullmatch(tenant_id) or not TENANT.fullmatch(project_id):
            raise ValueError("Invalid tenant/project scope")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)


class LearningCollector:
    """Quarantine, verify and admit a Runtime attempt without modifying Runtime."""

    def __init__(self, runtime_store, learning_store: TenantLearningStore):
        self.runtime_store = runtime_store
        self.learning_store = learning_store

    def collect(self, run_id: str, context: LearningContext, *, tenant_id: str,
                project_id: str) -> CollectionReceipt:
        TenantLearningStore._scope(tenant_id, project_id)
        context.validate()
        run = self.runtime_store.get_run(run_id)
        if run.status.value not in {"succeeded", "failed", "cancelled", "timed_out"}:
            raise ValueError("LearningCollector accepts terminal Runtime runs only")
        attempts = [attempt for stage in self.runtime_store.list_stages(run_id)
                    for attempt in self.runtime_store.list_attempts(stage.stage_run_id)]
        if not attempts:
            raise ValueError("Runtime run has no attempt")
        successful = [item for item in attempts if item.status.value == "succeeded"]
        attempt = successful[-1] if successful else attempts[-1]
        key = {"tenant": tenant_id, "project": project_id, "run": run_id,
               "attempt": attempt.attempt_id, "context": context.fingerprint,
               "parser": context.metric_parser_version}
        collection_id = f"collect-{_digest(key)[:24]}"
        existing = self._receipt(collection_id)
        if existing and existing.status in {"admitted", "rejected"}:
            return existing
        self._record(collection_id, tenant_id, project_id, run_id, attempt.attempt_id,
                     context, "quarantined", None, None)
        try:
            observation = RuntimeEvidenceExporter(self.runtime_store).export_run(run_id, context)
            self._record(collection_id, tenant_id, project_id, run_id, attempt.attempt_id,
                         context, "verified", None, None)
            observation_id = self.learning_store.admit(tenant_id, project_id, observation)
            self._record(collection_id, tenant_id, project_id, run_id, attempt.attempt_id,
                         context, "admitted", observation_id, None)
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"[:1000]
            self._record(collection_id, tenant_id, project_id, run_id, attempt.attempt_id,
                         context, "rejected", None, reason)
        return self._receipt(collection_id)  # type: ignore[return-value]

    def reject(self, run_id: str, context: LearningContext, *, tenant_id: str,
               project_id: str, run_status: str, reason: str) -> str:
        """Record a rejected run (failed / cancelled / timed-out) without touching
        the learning observations — keeps the knowledge base clean while keeping
        an audit trail (and optional future negative samples for offline RL)."""
        TenantLearningStore._scope(tenant_id, project_id)
        context.validate()
        attempts = [attempt for stage in self.runtime_store.list_stages(run_id)
                    for attempt in self.runtime_store.list_attempts(stage.stage_run_id)]
        attempt_id = attempts[-1].attempt_id if attempts else "no-attempt"
        metrics_snapshot = None
        if attempts:
            try:
                metrics_snapshot = json.dumps(
                    self.runtime_store.metrics(attempt_id),
                    ensure_ascii=False, default=str)
            except Exception:
                metrics_snapshot = None
        key = {"tenant": tenant_id, "project": project_id, "run": run_id,
               "attempt": attempt_id, "context": context.fingerprint}
        rejection_id = f"reject-{_digest(key)[:24]}"
        with self.learning_store._connect() as connection:
            connection.execute("""INSERT INTO learning_rejections_v1
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(rejection_id) DO UPDATE SET
                    run_status=excluded.run_status, reason=excluded.reason,
                    metrics_snapshot=excluded.metrics_snapshot""",
                (rejection_id, tenant_id, project_id, run_id, attempt_id,
                 context.fingerprint, run_status, reason[:2000], metrics_snapshot))
        return rejection_id

    def _record(self, collection_id: str, tenant_id: str, project_id: str, run_id: str,
                attempt_id: str, context: LearningContext, status: str,
                observation_id: str | None, reason: str | None) -> None:
        with self.learning_store._connect() as connection:
            connection.execute("""INSERT INTO learning_collection_v1 VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                ON CONFLICT(collection_id) DO UPDATE SET status=excluded.status,
                observation_id=excluded.observation_id, reason=excluded.reason,
                updated_at=datetime('now')""",
                (collection_id, tenant_id, project_id, run_id, attempt_id,
                 context.fingerprint, context.metric_parser_version, status,
                 observation_id, reason))

    def _receipt(self, collection_id: str) -> CollectionReceipt | None:
        with self.learning_store._connect() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM learning_collection_v1 WHERE collection_id = ?",
                                     (collection_id,)).fetchone()
        if row is None:
            return None
        return CollectionReceipt(**{key: row[key] for key in (
            "collection_id", "tenant_id", "project_id", "run_id", "attempt_id",
            "context_fingerprint", "parser_version", "status", "observation_id", "reason")})
