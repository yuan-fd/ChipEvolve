"""Durable checkpoints for long, externally evaluated platform pipelines.

The store deliberately knows nothing about RTL, LLMs, or EDA tools.  It only
provides an immutable identity plus a compare-and-update state document.  The
API/orchestration layer owns stage semantics, while Runtime remains the source
of truth for every tool execution.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


class PipelineCheckpointStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("""CREATE TABLE IF NOT EXISTS pipeline_checkpoints_v1 (
                pipeline_id TEXT PRIMARY KEY,
                pipeline_kind TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                owner_id TEXT,
                state_json TEXT NOT NULL,
                revision INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(pipeline_kind, subject_id, owner_id)
            )""")

    def create_or_get(self, *, pipeline_kind: str, subject_id: str,
                      owner_id: str | None, initial_state: Mapping[str, Any]) -> dict[str, Any]:
        if not pipeline_kind.strip() or not subject_id.strip():
            raise ValueError("pipeline_kind and subject_id are required")
        now = datetime.now(timezone.utc).isoformat()
        identifier = f"pipeline-{uuid.uuid4().hex}"
        encoded = json.dumps(dict(initial_state), ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM pipeline_checkpoints_v1
                   WHERE pipeline_kind=? AND subject_id=? AND owner_id IS ?""",
                (pipeline_kind, subject_id, owner_id),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO pipeline_checkpoints_v1 VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
                    (identifier, pipeline_kind, subject_id, owner_id, encoded, now, now),
                )
            else:
                identifier = str(row["pipeline_id"])
            connection.commit()
        return self.get(identifier)

    def get(self, pipeline_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM pipeline_checkpoints_v1 WHERE pipeline_id=?", (pipeline_id,),
            ).fetchone()
        if row is None:
            raise KeyError(pipeline_id)
        return {
            "pipeline_id": row["pipeline_id"], "pipeline_kind": row["pipeline_kind"],
            "subject_id": row["subject_id"], "owner_id": row["owner_id"],
            "state": json.loads(row["state_json"]), "revision": int(row["revision"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def list(self, *, pipeline_kind: str | None = None,
             owner_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Return durable pipeline records for read-only product evidence views."""
        if not 1 <= int(limit) <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        clauses: list[str] = []
        values: list[Any] = []
        if pipeline_kind is not None:
            clauses.append("pipeline_kind=?")
            values.append(pipeline_kind)
        if owner_id is not None:
            clauses.append("owner_id IS ?")
            values.append(owner_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT pipeline_id FROM pipeline_checkpoints_v1{where} "
                "ORDER BY updated_at DESC LIMIT ?",
                (*values, int(limit)),
            ).fetchall()
        return [self.get(str(row["pipeline_id"])) for row in rows]

    def save(self, pipeline_id: str, state: Mapping[str, Any], *, expected_revision: int) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        encoded = json.dumps(dict(state), ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            changed = connection.execute(
                """UPDATE pipeline_checkpoints_v1
                   SET state_json=?, revision=revision+1, updated_at=?
                   WHERE pipeline_id=? AND revision=?""",
                (encoded, now, pipeline_id, int(expected_revision)),
            )
            if changed.rowcount != 1:
                if connection.execute(
                    "SELECT 1 FROM pipeline_checkpoints_v1 WHERE pipeline_id=?", (pipeline_id,),
                ).fetchone() is None:
                    raise KeyError(pipeline_id)
                raise ValueError("pipeline checkpoint revision conflict")
        return self.get(pipeline_id)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection
