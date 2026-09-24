"""Durable worker and queue health read models."""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any

from .store import RuntimeStore


def touch(store: RuntimeStore, worker_id: str, *, attempt_id: str | None = None) -> None:
    now = time.time()
    with store._lock:
        active = store._connection.execute(
            "SELECT attempt_id FROM runtime_attempts WHERE worker_id = ? "
            "AND status = 'running' ORDER BY started_at",
            (worker_id,),
        ).fetchall()
        active_ids = [str(row["attempt_id"]) for row in active]
        if attempt_id is not None and attempt_id not in active_ids:
            active_ids.insert(0, attempt_id)
        current = active_ids[0] if active_ids else None
        store._connection.execute(
            "INSERT INTO runtime_workers "
            "(worker_id,pid,last_seen_at,current_attempt_id,state) VALUES (?,?,?,?,?) "
            "ON CONFLICT(worker_id) DO UPDATE SET pid=excluded.pid, "
            "last_seen_at=excluded.last_seen_at, "
            "current_attempt_id=excluded.current_attempt_id, state=excluded.state",
            (worker_id, os.getpid(), now, current,
             "running" if active_ids else "idle"),
        )


def snapshot(store: RuntimeStore, *, stale_after: float = 30.0) -> list[dict[str, Any]]:
    cutoff = time.time() - stale_after
    with store._lock:
        rows = store._connection.execute(
            "SELECT worker_id,pid,last_seen_at,current_attempt_id,state "
            "FROM runtime_workers ORDER BY worker_id"
        ).fetchall()
    return [
        {
            "worker_id": row["worker_id"], "pid": row["pid"],
            "last_seen_at": row["last_seen_at"],
            "current_attempt_id": row["current_attempt_id"],
            "active_attempt_ids": _active_attempts(store, row["worker_id"]),
            "state": row["state"] if row["last_seen_at"] >= cutoff else "stale",
        }
        for row in rows
    ]


def _active_attempts(store: RuntimeStore, worker_id: str) -> list[str]:
    with store._lock:
        rows = store._connection.execute(
            "SELECT attempt_id FROM runtime_attempts WHERE worker_id = ? "
            "AND status = 'running' ORDER BY started_at", (worker_id,)
        ).fetchall()
    return [str(row["attempt_id"]) for row in rows]


def queue(store: RuntimeStore) -> dict[str, Any]:
    with store._lock:
        row = store._connection.execute(
            "SELECT COUNT(*) AS waiting, MIN(r.created_at) AS oldest, "
            "SUM(CASE WHEN s.status = 'queued' THEN 1 ELSE 0 END) AS queued, "
            "SUM(CASE WHEN s.status = 'retry_wait' THEN 1 ELSE 0 END) AS retry_wait "
            "FROM runtime_runs r JOIN runtime_stage_runs s ON s.run_id = r.run_id "
            "WHERE s.status IN ('queued','retry_wait')"
        ).fetchone()
    oldest = row["oldest"]
    age = 0.0
    if oldest:
        age = max(0.0, time.time() - datetime.fromisoformat(oldest).timestamp())
    return {
        "waiting": int(row["waiting"]), "queued": int(row["queued"] or 0),
        "retry_wait": int(row["retry_wait"] or 0),
        "oldest_created_at": oldest, "oldest_age_seconds": age,
    }
