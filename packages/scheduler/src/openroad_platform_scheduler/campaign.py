"""Durable bounded campaigns layered over the authoritative Workflow Runtime."""

from __future__ import annotations

import json
import fcntl
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from openroad_platform_contracts import RuntimeStatus, TaskSpec, TERMINAL_RUNTIME_STATUSES

from .runtime import WorkflowRuntime


@dataclass(frozen=True)
class CampaignMember:
    member_id: str
    campaign_id: str
    ordinal: int
    task_spec: TaskSpec
    run_id: str | None


class CampaignStore:
    """Small metadata store; all execution truth remains in RuntimeStore."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS campaigns (
                    campaign_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    max_parallel INTEGER NOT NULL CHECK(max_parallel > 0),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS campaign_members (
                    member_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
                    ordinal INTEGER NOT NULL,
                    task_id TEXT NOT NULL,
                    task_spec_json TEXT NOT NULL,
                    run_id TEXT,
                    UNIQUE(campaign_id, ordinal),
                    UNIQUE(campaign_id, task_id),
                    UNIQUE(run_id)
                );
            """)

    def create(self, name: str, tasks: Iterable[TaskSpec], *, max_parallel: int = 1,
               campaign_id: str | None = None) -> str:
        items = tuple(tasks)
        if not name.strip() or not items:
            raise ValueError("Campaign name and at least one task are required")
        if not isinstance(max_parallel, int) or not 1 <= max_parallel <= 32:
            raise ValueError("max_parallel must be between 1 and 32")
        for task in items:
            task.validate()
            if task.plugin_id is None:
                raise ValueError("Campaign v1 only supports direct plugin tasks")
        identifier = campaign_id or f"campaign-{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO campaigns VALUES (?, ?, ?, ?)",
                               (identifier, name.strip(), max_parallel, now))
            for ordinal, task in enumerate(items, 1):
                connection.execute(
                    "INSERT INTO campaign_members VALUES (?, ?, ?, ?, ?, NULL)",
                    (f"member-{uuid.uuid4().hex}", identifier, ordinal, task.task_id,
                     json.dumps(task.to_dict(), ensure_ascii=False)),
                )
            connection.commit()
        return identifier

    def get(self, campaign_id: str) -> dict:
        with self._connect() as connection:
            campaign = connection.execute(
                "SELECT * FROM campaigns WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()
            if campaign is None:
                raise KeyError(f"Unknown campaign: {campaign_id}")
        return {"campaign_id": campaign["campaign_id"], "name": campaign["name"],
                "max_parallel": campaign["max_parallel"],
                "created_at": campaign["created_at"]}

    def list(self, *, limit: int = 100) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM campaigns ORDER BY created_at DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [{"campaign_id": row["campaign_id"], "name": row["name"],
                 "max_parallel": row["max_parallel"], "created_at": row["created_at"]}
                for row in rows]

    def members(self, campaign_id: str) -> list[CampaignMember]:
        self.get(campaign_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM campaign_members WHERE campaign_id = ? ORDER BY ordinal",
                (campaign_id,),
            ).fetchall()
        return [CampaignMember(row["member_id"], row["campaign_id"], row["ordinal"],
                               TaskSpec.from_dict(json.loads(row["task_spec_json"])),
                               row["run_id"]) for row in rows]

    def bind(self, member_id: str, run_id: str) -> None:
        with self._connect() as connection:
            changed = connection.execute(
                "UPDATE campaign_members SET run_id = ? WHERE member_id = ? AND run_id IS NULL",
                (run_id, member_id),
            )
            if changed.rowcount == 0:
                row = connection.execute(
                    "SELECT run_id FROM campaign_members WHERE member_id = ?", (member_id,)
                ).fetchone()
                if row is None or row["run_id"] != run_id:
                    raise ValueError("Campaign member is already bound to another run")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


class CampaignManager:
    def __init__(self, store: CampaignStore, runtime: WorkflowRuntime):
        self.store = store
        self.runtime = runtime

    def ensure_runs(self, campaign_id: str) -> tuple[str, ...]:
        run_ids = []
        lock_path = self.store.path.with_suffix(self.store.path.suffix + ".bind.lock")
        with lock_path.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for member in self.store.members(campaign_id):
                run_id = member.run_id
                if run_id is None:
                    existing = self.runtime.store.find_run_by_task_id(member.task_spec.task_id)
                    run = existing or self.runtime.submit(member.task_spec)
                    run_id = run.run_id
                    self.store.bind(member.member_id, run_id)
                run_ids.append(run_id)
        return tuple(run_ids)

    def describe(self, campaign_id: str) -> dict:
        campaign = self.store.get(campaign_id)
        members = []
        counts: dict[str, int] = {}
        for member in self.store.members(campaign_id):
            run = self.runtime.store.get_run(member.run_id) if member.run_id else None
            status = run.status.value if run else "unbound"
            if run and run.status not in TERMINAL_RUNTIME_STATUSES:
                stages = self.runtime.store.list_stages(run.run_id)
                if any(stage.status is RuntimeStatus.RETRY_WAIT for stage in stages):
                    status = RuntimeStatus.RETRY_WAIT.value
            counts[status] = counts.get(status, 0) + 1
            members.append({"member_id": member.member_id, "ordinal": member.ordinal,
                            "task_id": member.task_spec.task_id, "run_id": member.run_id,
                            "status": status})
        terminal = bool(members) and all(
            item["status"] in {status.value for status in TERMINAL_RUNTIME_STATUSES}
            for item in members
        )
        return {**campaign, "status": "finished" if terminal else "active",
                "counts": counts, "members": members}

    def run_until_terminal(self, campaign_id: str, *, timeout_seconds: float = 60) -> dict:
        self.ensure_runs(campaign_id)
        deadline = time.monotonic() + timeout_seconds
        max_parallel = self.store.get(campaign_id)["max_parallel"]
        while time.monotonic() < deadline:
            self.runtime.store.expire_leases()
            view = self.describe(campaign_id)
            ready = [item for item in view["members"] if item["status"] in {
                RuntimeStatus.QUEUED.value, RuntimeStatus.RETRY_WAIT.value,
            }]
            active = sum(item["status"] in {
                RuntimeStatus.PREPARING.value, RuntimeStatus.RUNNING.value,
                RuntimeStatus.CANCEL_REQUESTED.value,
            } for item in view["members"])
            slots = max(0, max_parallel - active)
            if not ready:
                if view["status"] == "finished":
                    return view
                time.sleep(0.01)
                continue
            if slots == 0:
                time.sleep(0.01)
                continue
            with ThreadPoolExecutor(max_workers=max_parallel) as pool:
                futures = [pool.submit(self.runtime.execute_once, item["run_id"])
                           for item in ready[:slots]]
                for future in futures:
                    future.result()
        raise TimeoutError(f"Campaign did not finish within {timeout_seconds} seconds")

    def cancel(self, campaign_id: str) -> dict:
        self.ensure_runs(campaign_id)
        for member in self.store.members(campaign_id):
            run = self.runtime.store.get_run(member.run_id)
            if run.status not in TERMINAL_RUNTIME_STATUSES:
                self.runtime.store.request_cancel(run.run_id)
        return self.describe(campaign_id)
