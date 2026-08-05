"""Durable bounded campaigns layered over the authoritative Workflow Runtime."""

from __future__ import annotations

import json
import fcntl
import dataclasses
import itertools
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from openroad_platform_contracts import RuntimeStatus, TaskSpec, TERMINAL_RUNTIME_STATUSES

from .runtime import WorkflowRuntime
from .nl_control import LimitedReActController


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
                CREATE TABLE IF NOT EXISTS campaign_stage_policies (
                    campaign_id TEXT PRIMARY KEY REFERENCES campaigns(campaign_id),
                    policy_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS campaign_decisions (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_id TEXT NOT NULL UNIQUE,
                    decision_key TEXT NOT NULL UNIQUE,
                    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
                    run_id TEXT, kind TEXT NOT NULL, payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS campaign_repairs (
                    parent_run_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL,
                    child_member_id TEXT NOT NULL, action_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
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

    def append_member(self, campaign_id: str, task: TaskSpec) -> str:
        task.validate()
        member_id = f"member-{uuid.uuid4().hex}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            ordinal = connection.execute(
                "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM campaign_members WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO campaign_members VALUES (?, ?, ?, ?, ?, NULL)",
                (member_id, campaign_id, ordinal, task.task_id,
                 json.dumps(task.to_dict(), ensure_ascii=False)),
            )
        return member_id

    def set_stage_policy(self, campaign_id: str, policy: Mapping[str, Any]) -> None:
        self.get(campaign_id)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO campaign_stage_policies VALUES (?, ?)",
                (campaign_id, json.dumps(dict(policy), ensure_ascii=False)),
            )

    def stage_policy(self, campaign_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT policy_json FROM campaign_stage_policies WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Campaign has no stage-aware policy: {campaign_id}")
        return json.loads(row["policy_json"])

    def record_decision(self, campaign_id: str, *, decision_key: str,
                        run_id: str | None, kind: str, payload: Mapping[str, Any]) -> bool:
        with self._connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO campaign_decisions
                       (decision_id, decision_key, campaign_id, run_id, kind, payload_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (f"decision-{uuid.uuid4().hex}", decision_key, campaign_id, run_id,
                     kind, json.dumps(dict(payload), ensure_ascii=False),
                     datetime.now(timezone.utc).isoformat()),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def decisions(self, campaign_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM campaign_decisions WHERE campaign_id = ? ORDER BY sequence",
                (campaign_id,),
            ).fetchall()
        return [{"decision_id": row["decision_id"], "decision_key": row["decision_key"],
                 "run_id": row["run_id"], "kind": row["kind"],
                 "payload": json.loads(row["payload_json"]),
                 "created_at": row["created_at"]} for row in rows]

    def record_repair(self, campaign_id: str, *, parent_run_id: str,
                      child_member_id: str, action: Mapping[str, Any]) -> bool:
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO campaign_repairs VALUES (?, ?, ?, ?, ?)",
                    (parent_run_id, campaign_id, child_member_id,
                     json.dumps(dict(action), ensure_ascii=False),
                     datetime.now(timezone.utc).isoformat()),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def has_repair(self, parent_run_id: str) -> bool:
        with self._connect() as connection:
            return connection.execute(
                "SELECT 1 FROM campaign_repairs WHERE parent_run_id = ?", (parent_run_id,)
            ).fetchone() is not None

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


GRID_PARAMETERS = {
    "clock_period_ns": (0.01, 1000.0),
    "core_utilization_pct": (1.0, 99.0),
    "place_density": (0.01, 1.0),
    "stage_timeout_seconds": (1.0, 86_400.0),
}
TOOL_STAGES = ("synth", "floorplan", "place", "cts", "route", "finish")


class StageAwareCampaignManager(CampaignManager):
    """Bounded grid execution driven by authoritative per-stage Runtime events."""

    def create_grid(
        self, name: str, base_task: TaskSpec, parameter_grid: Mapping[str, Sequence[Any]], *,
        max_parallel: int = 1, stage_budgets: Mapping[str, float] | None = None,
        objective_metric: str | None = None, direction: str = "min", top_k: int = 3,
        max_repairs: int = 2, max_total_runs: int = 64,
    ) -> str:
        base_task.validate()
        unknown = sorted(set(parameter_grid) - set(GRID_PARAMETERS))
        if unknown:
            raise ValueError(f"Unsupported grid parameters: {', '.join(unknown)}")
        if direction not in {"min", "max"}:
            raise ValueError("direction must be min or max")
        if not 1 <= top_k <= 20 or not 0 <= max_repairs <= 5:
            raise ValueError("Invalid Top-K or repair budget")
        names = tuple(parameter_grid)
        values = []
        for name_ in names:
            choices = tuple(parameter_grid[name_])
            if not choices:
                raise ValueError(f"Grid parameter {name_} has no values")
            low, high = GRID_PARAMETERS[name_]
            normalized = []
            for choice in choices:
                number = float(choice)
                if not low <= number <= high:
                    raise ValueError(f"Grid parameter {name_} is outside policy")
                normalized.append(int(number) if name_ == "stage_timeout_seconds" else number)
            values.append(tuple(normalized))
        combinations = list(itertools.product(*values)) if names else [()]
        if len(combinations) > max_total_runs:
            raise ValueError("Parameter grid exceeds max_total_runs")
        tasks = []
        for ordinal, combination in enumerate(combinations, 1):
            parameters = {**base_task.parameters, **dict(zip(names, combination))}
            candidate = dataclasses.replace(
                base_task, task_id=f"campaign-{uuid.uuid4().hex}", parameters=parameters,
                labels={**base_task.labels, "candidate_ordinal": str(ordinal),
                        "repair_depth": "0"},
            )
            candidate.validate()
            tasks.append(candidate)
        budgets = {str(stage): float(seconds) for stage, seconds in (stage_budgets or {}).items()}
        if any(stage not in TOOL_STAGES or seconds <= 0 or seconds > 86_400
               for stage, seconds in budgets.items()):
            raise ValueError("Invalid per-stage wall-clock budget")
        campaign_id = self.store.create(name, tasks, max_parallel=max_parallel)
        self.store.set_stage_policy(campaign_id, {
            "schema_version": 1, "stage_budgets": budgets,
            "objective_metric": objective_metric, "direction": direction, "top_k": top_k,
            "max_repairs": max_repairs, "max_total_runs": max_total_runs,
            "pruning_policy": "stage_wall_clock_v1",
        })
        return campaign_id

    def describe(self, campaign_id: str) -> dict:
        view = super().describe(campaign_id)
        view["stage_policy"] = self.store.stage_policy(campaign_id)
        view["decisions"] = self.store.decisions(campaign_id)
        view["ranking"] = self._ranking(campaign_id)
        return view

    def run_until_terminal(self, campaign_id: str, *, timeout_seconds: float = 60) -> dict:
        deadline = time.monotonic() + timeout_seconds
        policy = self.store.stage_policy(campaign_id)
        max_parallel = self.store.get(campaign_id)["max_parallel"]
        futures: dict[str, Any] = {}
        pool = ThreadPoolExecutor(max_workers=max_parallel)
        try:
            while time.monotonic() < deadline:
                self.ensure_runs(campaign_id)
                self.runtime.store.expire_leases()
                for run_id, future in list(futures.items()):
                    if future.done():
                        future.result()
                        del futures[run_id]
                self._prune_over_budget(campaign_id, policy)
                members = self.store.members(campaign_id)
                active_ids = set(futures)
                ready = []
                for member in members:
                    if member.run_id in active_ids:
                        continue
                    run = self.runtime.store.get_run(member.run_id)
                    if run.status in {RuntimeStatus.QUEUED, RuntimeStatus.RETRY_WAIT}:
                        ready.append(member.run_id)
                for run_id in ready[:max(0, max_parallel - len(futures))]:
                    futures[run_id] = pool.submit(self.runtime.execute_once, run_id)
                if not futures and not ready:
                    if self._create_repairs(campaign_id, policy):
                        continue
                    view = self.describe(campaign_id)
                    if view["status"] == "finished":
                        self._record_top_k(campaign_id, view["ranking"], policy)
                        return self.describe(campaign_id)
                time.sleep(0.02)
        finally:
            pool.shutdown(wait=True)
        raise TimeoutError(f"Stage-aware campaign did not finish within {timeout_seconds} seconds")

    def _prune_over_budget(self, campaign_id: str, policy: Mapping[str, Any]) -> None:
        budgets = policy.get("stage_budgets", {})
        now = datetime.now(timezone.utc)
        for member in self.store.members(campaign_id):
            if not member.run_id:
                continue
            run = self.runtime.store.get_run(member.run_id)
            if run.status not in {RuntimeStatus.RUNNING, RuntimeStatus.CANCEL_REQUESTED}:
                continue
            events = self.runtime.store.events(run.run_id)
            started: dict[str, dict[str, Any]] = {}
            finished = set()
            for event in events:
                stage = event["payload"].get("tool_stage")
                if event["event_type"] == "tool.stage.started" and stage:
                    started[stage] = event
                elif event["event_type"] == "tool.stage.finished" and stage:
                    finished.add(stage)
            for stage, event in started.items():
                if stage in finished or stage not in budgets:
                    continue
                elapsed = (now - datetime.fromisoformat(event["occurred_at"])).total_seconds()
                if elapsed <= float(budgets[stage]):
                    continue
                key = f"stage-budget:{run.run_id}:{stage}"
                if self.store.record_decision(
                    campaign_id, decision_key=key, run_id=run.run_id, kind="prune",
                    payload={"tool_stage": stage, "elapsed_seconds": elapsed,
                             "budget_seconds": budgets[stage],
                             "policy": "stage_wall_clock_v1"},
                ):
                    self.runtime.store.request_cancel(run.run_id)

    def _create_repairs(self, campaign_id: str, policy: Mapping[str, Any]) -> bool:
        max_repairs = int(policy.get("max_repairs", 0))
        max_total = int(policy.get("max_total_runs", 64))
        if max_repairs == 0:
            return False
        created = False
        for member in list(self.store.members(campaign_id)):
            if len(self.store.members(campaign_id)) >= max_total or not member.run_id:
                break
            run = self.runtime.store.get_run(member.run_id)
            if run.status is not RuntimeStatus.FAILED or self.store.has_repair(run.run_id):
                continue
            depth = int(member.task_spec.labels.get("repair_depth", "0"))
            if depth >= max_repairs:
                continue
            attempt = self._last_attempt(run.run_id)
            if attempt is None or not attempt.failure:
                continue
            failure = dict(attempt.failure)
            failure["category"] = _repair_category(failure)
            failure["evidence_refs"] = [f"runtime:{run.run_id}:attempt:{attempt.attempt_id}"]
            controller = LimitedReActController(max_repairs=max_repairs)
            action = controller.decide(member.task_spec, failure)
            if action.action_type == "stop":
                self.store.record_decision(
                    campaign_id, decision_key=f"repair-stop:{run.run_id}", run_id=run.run_id,
                    kind="repair_stopped", payload=action.to_dict(),
                )
                continue
            child = controller.apply(member.task_spec, action)
            child = dataclasses.replace(
                child, labels={**child.labels, "repair_depth": str(depth + 1),
                               "parent_run_id": run.run_id},
            )
            child_member = self.store.append_member(campaign_id, child)
            if self.store.record_repair(
                campaign_id, parent_run_id=run.run_id, child_member_id=child_member,
                action=action.to_dict(),
            ):
                self.store.record_decision(
                    campaign_id, decision_key=f"repair:{run.run_id}", run_id=run.run_id,
                    kind="repair_created",
                    payload={"action": action.to_dict(), "child_member_id": child_member},
                )
                created = True
        return created

    def _last_attempt(self, run_id: str):
        stages = self.runtime.store.list_stages(run_id)
        attempts = [attempt for stage in stages
                    for attempt in self.runtime.store.list_attempts(stage.stage_run_id)]
        return attempts[-1] if attempts else None

    def _ranking(self, campaign_id: str) -> list[dict[str, Any]]:
        policy = self.store.stage_policy(campaign_id)
        metric_name = policy.get("objective_metric")
        if not metric_name:
            return []
        ranked = []
        for member in self.store.members(campaign_id):
            if not member.run_id:
                continue
            run = self.runtime.store.get_run(member.run_id)
            if run.status is not RuntimeStatus.SUCCEEDED:
                continue
            attempt = self._last_attempt(run.run_id)
            if attempt is None:
                continue
            metric = next((item for item in self.runtime.store.metrics(attempt.attempt_id)
                           if item["name"] == metric_name), None)
            if metric is None or not isinstance(metric["value"], (int, float)):
                continue
            ranked.append({"run_id": run.run_id, "member_id": member.member_id,
                           "metric": metric_name, "value": metric["value"],
                           "parameters": member.task_spec.parameters})
        reverse = policy.get("direction") == "max"
        ranked.sort(key=lambda item: item["value"], reverse=reverse)
        return ranked[:int(policy.get("top_k", 3))]

    def _record_top_k(self, campaign_id: str, ranking: list[dict[str, Any]],
                      policy: Mapping[str, Any]) -> None:
        self.store.record_decision(
            campaign_id, decision_key=f"top-k:{campaign_id}", run_id=None, kind="top_k",
            payload={"objective_metric": policy.get("objective_metric"),
                     "direction": policy.get("direction"), "ranking": ranking},
        )


def _repair_category(failure: Mapping[str, Any]) -> str:
    category = str(failure.get("category") or "unknown")
    message = str(failure.get("message") or "").lower()
    if category in {"timeout", "worker_lost", "transient_io", "congestion", "placement_failed"}:
        return category
    if "timeout" in message or "timed out" in message:
        return "timeout"
    if "congestion" in message or "overflow" in message:
        return "congestion"
    if "placement" in message and ("fail" in message or "error" in message):
        return "placement_failed"
    if "pdn-0185" in message or "insufficient width" in message:
        return "pdn_insufficient_area"
    return category
