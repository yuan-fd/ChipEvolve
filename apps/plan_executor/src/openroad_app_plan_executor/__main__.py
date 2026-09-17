"""Durable, ordered execution plans over the kernel's single-task API."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

SERVICE_NAME = "plan_executor"
DEFAULT_PORT = 8840
DB_FILENAME = "plan_executor.sqlite"
MAX_STEPS = 64
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
ACTIVE = frozenset({"queued", "preparing", "running", "retry_wait", "cancel_requested"})
FAILED = frozenset({"failed", "cancelled", "timed_out", "lost"})
PLATFORM_FAILURES = frozenset({
    "cancelled", "lease_expired", "protocol_error", "resource_exceeded",
    "runtime_error", "timeout",
})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    plan_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    failure_json TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS plan_steps (
    plan_id TEXT NOT NULL REFERENCES plans(plan_id),
    ordinal INTEGER NOT NULL,
    step_id TEXT NOT NULL,
    task_json TEXT NOT NULL,
    bindings_json TEXT NOT NULL,
    run_id TEXT,
    status TEXT NOT NULL,
    failure_json TEXT,
    PRIMARY KEY (plan_id, step_id),
    UNIQUE (plan_id, ordinal)
);
"""


class PlanError(Exception):
    """A plan request or transition the service cannot honour."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class PlanStore:
    """The plan service's state; run evidence remains in the kernel."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = DELETE")
        return connection

    def create(self, payload: Mapping[str, Any]) -> str:
        plan_id = str(payload.get("plan_id") or f"plan-{uuid.uuid4().hex}")
        steps = payload.get("steps")
        self._validate(plan_id, steps)
        assert isinstance(steps, list)
        with self._lock, self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO plans (plan_id,status,created_at) VALUES (?,?,?)",
                    (plan_id, "queued", time.time()),
                )
                for ordinal, step in enumerate(steps):
                    connection.execute(
                        "INSERT INTO plan_steps (plan_id,ordinal,step_id,task_json,"
                        "bindings_json,status) VALUES (?,?,?,?,?,?)",
                        (plan_id, ordinal, step["step_id"],
                         json.dumps(step["task"], sort_keys=True),
                         json.dumps(step.get("bindings", []), sort_keys=True),
                         "queued"),
                    )
            except sqlite3.IntegrityError as exc:
                raise PlanError(f"plan_id {plan_id!r} already exists", 409) from exc
        return plan_id

    @staticmethod
    def _validate(plan_id: str, steps: Any) -> None:
        if not IDENTIFIER.fullmatch(plan_id):
            raise PlanError("plan_id must be a non-empty identifier")
        if not isinstance(steps, list) or not 0 < len(steps) <= MAX_STEPS:
            raise PlanError(f"steps must contain between 1 and {MAX_STEPS} entries")
        seen: set[str] = set()
        for step in steps:
            if not isinstance(step, dict):
                raise PlanError("every step must be an object")
            step_id = step.get("step_id")
            if not isinstance(step_id, str) or not IDENTIFIER.fullmatch(step_id):
                raise PlanError("every step needs an identifier step_id")
            if step_id in seen:
                raise PlanError(f"duplicate step_id {step_id!r}")
            if not isinstance(step.get("task"), dict):
                raise PlanError(f"step {step_id!r} needs a task object")
            bindings = step.get("bindings", [])
            if not isinstance(bindings, list):
                raise PlanError(f"step {step_id!r} bindings must be a list")
            for binding in bindings:
                PlanStore._validate_binding(step_id, binding, seen)
            seen.add(step_id)

    @staticmethod
    def _validate_binding(step_id: str, binding: Any, earlier: set[str]) -> None:
        if not isinstance(binding, dict):
            raise PlanError(f"step {step_id!r} has a non-object binding")
        if binding.get("from_step") not in earlier:
            raise PlanError(
                f"step {step_id!r} binding must refer to an earlier step"
            )
        for field in ("artifact_kind", "destination"):
            if not isinstance(binding.get(field), str) or not binding[field]:
                raise PlanError(f"step {step_id!r} binding needs {field}")
        selector = binding.get("metadata", {})
        if not isinstance(selector, dict):
            raise PlanError(f"step {step_id!r} binding metadata must be an object")

    def get(self, plan_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            plan = connection.execute(
                "SELECT * FROM plans WHERE plan_id = ?", (plan_id,)
            ).fetchone()
            if plan is None:
                raise PlanError(f"unknown plan {plan_id!r}", 404)
            rows = connection.execute(
                "SELECT * FROM plan_steps WHERE plan_id = ? ORDER BY ordinal",
                (plan_id,),
            ).fetchall()
        status = str(plan["status"])
        return {
            "plan_id": plan_id,
            "status": status,
            "execution_valid": (True if status == "succeeded" else
                                False if status in FAILED else None),
            "failure": _json_or_none(plan["failure_json"]),
            "steps": [self._step(row) for row in rows],
        }

    @staticmethod
    def _step(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "step_id": row["step_id"], "ordinal": row["ordinal"],
            "task": json.loads(row["task_json"]),
            "bindings": json.loads(row["bindings_json"]),
            "run_id": row["run_id"], "status": row["status"],
            "failure": _json_or_none(row["failure_json"]),
        }

    def active(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT plan_id FROM plans WHERE status IN ('queued','running') "
                "ORDER BY created_at"
            ).fetchall()
        return [str(row["plan_id"]) for row in rows]

    def submitted(self, plan_id: str, step_id: str, run_id: str) -> None:
        self._update_step(plan_id, step_id, "queued", run_id=run_id)
        self._update_plan(plan_id, "running")

    def step_status(self, plan_id: str, step_id: str, status: str,
                    failure: Mapping[str, Any] | None = None) -> None:
        self._update_step(plan_id, step_id, status, failure=failure)

    def finish(self, plan_id: str, status: str,
               failure: Mapping[str, Any] | None = None) -> None:
        self._update_plan(plan_id, status, failure)

    def _update_step(self, plan_id: str, step_id: str, status: str, *,
                     run_id: str | None = None,
                     failure: Mapping[str, Any] | None = None) -> None:
        fields = ["status = ?", "failure_json = ?"]
        values: list[Any] = [status, json.dumps(failure) if failure else None]
        if run_id is not None:
            fields.append("run_id = ?")
            values.append(run_id)
        values.extend((plan_id, step_id))
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE plan_steps SET {', '.join(fields)} "
                "WHERE plan_id = ? AND step_id = ?", values,
            )

    def _update_plan(self, plan_id: str, status: str,
                     failure: Mapping[str, Any] | None = None) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE plans SET status = ?, failure_json = ? WHERE plan_id = ?",
                (status, json.dumps(failure) if failure else None, plan_id),
            )


class PlanExecutor:
    """Advance each plan by one observable transition per cycle."""

    def __init__(self, store: PlanStore, client: Any):
        self.store = store
        self.client = client

    def cycle(self) -> int:
        advanced = 0
        for plan_id in self.store.active():
            try:
                self._advance(plan_id)
            except PlanError as exc:
                self.store.finish(plan_id, "failed", {
                    "source": "platform", "category": "artifact_binding_error",
                    "message": str(exc), "retryable": False,
                })
            advanced += 1
        return advanced

    def _advance(self, plan_id: str) -> None:
        plan = self.store.get(plan_id)
        pending = next(
            (step for step in plan["steps"] if step["status"] != "succeeded"),
            None,
        )
        if pending is None:
            self.store.finish(plan_id, "succeeded")
            return
        if pending["run_id"] is None:
            submitted = self.client.submit(
                self._task_for(plan, pending), idempotent=True
            )
            self.store.submitted(
                plan_id, pending["step_id"], submitted["run"]["run_id"]
            )
            return

        detail = self.client.run(pending["run_id"])
        status = str(detail["status"])
        if status in ACTIVE:
            self.store.step_status(plan_id, pending["step_id"], status)
            return
        if status == "succeeded":
            self.store.step_status(plan_id, pending["step_id"], status)
            if all(step["status"] == "succeeded" or
                   step["step_id"] == pending["step_id"]
                   for step in plan["steps"]):
                self.store.finish(plan_id, "succeeded")
            return
        failure = classify_failure(detail)
        self.store.step_status(plan_id, pending["step_id"], status, failure)
        self.store.finish(plan_id, status if status in FAILED else "failed", failure)

    def _task_for(self, plan: Mapping[str, Any], step: Mapping[str, Any]
                  ) -> dict[str, Any]:
        task = json.loads(json.dumps(step["task"]))
        staged = list(task.get("staged_inputs") or [])
        by_id = {item["step_id"]: item for item in plan["steps"]}
        for binding in step["bindings"]:
            source = by_id[binding["from_step"]]
            artifacts = self.client.artifacts(source["run_id"])
            selector = binding.get("metadata") or {}
            matches = [artifact for artifact in artifacts
                       if artifact.get("kind") == binding["artifact_kind"]
                       and all((artifact.get("metadata") or {}).get(key) == value
                               for key, value in selector.items())]
            if len(matches) != 1:
                raise PlanError(
                    f"binding from {binding['from_step']!r} expected one "
                    f"{binding['artifact_kind']!r} artifact, found {len(matches)}"
                )
            staged.append({
                "artifact_id": matches[0]["artifact_id"],
                "destination": binding["destination"], "required": True,
            })
        if staged:
            task["staged_inputs"] = staged
        return task


def classify_failure(detail: Mapping[str, Any]) -> dict[str, Any]:
    attempts = [attempt for stage in detail.get("stages", [])
                for attempt in stage.get("attempts", [])]
    recorded = attempts[-1].get("failure") if attempts else None
    failure = dict(recorded or {})
    category = str(
        failure.get("category") or detail.get("terminal_reason")
        or detail.get("status") or "unknown"
    )
    failure.update({
        "source": "platform" if category in PLATFORM_FAILURES else "plugin",
        "category": category,
        "message": str(failure.get("message") or category),
        "retryable": bool(failure.get("retryable", False)),
    })
    return failure


def _json_or_none(raw: str | None) -> Any:
    return json.loads(raw) if raw else None


class Handler(BaseHTTPRequestHandler):
    server_version = "openroad-app-plan_executor/0.1"

    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_error(404)
            return
        body = json.dumps({"app": SERVICE_NAME, "status": "ok"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Execution Plan Service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
