"""The execution-plan lifecycle, independent of any concrete EDA plugin."""

from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import openroad_app_plan_executor.__main__ as plan_executor
from openroad_app_plan_executor.__main__ import (
    PlanError,
    PlanExecutor,
    PlanStore,
    build_handler,
)
from openroad_platform_client import KernelError, KernelUnavailable


class FakeKernel:
    """The smallest real state machine the plan service needs from the kernel."""

    def __init__(self) -> None:
        self.submitted: list[dict] = []
        self.statuses: dict[str, str] = {}
        self.outputs: dict[str, list[dict]] = {}
        self.failures: dict[str, dict] = {}
        self.by_idempotency_key: dict[str, str] = {}

    def submit(self, task, *, idempotent=False, idempotency_key=None):
        if idempotency_key in self.by_idempotency_key:
            run_id = self.by_idempotency_key[idempotency_key]
            return {"run": {"run_id": run_id, "status": self.statuses[run_id]}}
        self.submitted.append(dict(task))
        run_id = f"run-{len(self.submitted)}"
        self.statuses[run_id] = "queued"
        if idempotency_key is not None:
            self.by_idempotency_key[idempotency_key] = run_id
        return {"run": {"run_id": run_id, "status": "queued"}}

    def run(self, run_id):
        failure = self.failures.get(run_id)
        attempt = {"attempt_id": f"attempt-{run_id}", "status": self.statuses[run_id],
                   "failure": failure}
        return {"run_id": run_id, "status": self.statuses[run_id],
                "terminal_reason": failure and failure["category"],
                "stages": [{"attempts": [attempt]}]}

    def artifacts(self, run_id):
        return self.outputs.get(run_id, [])

    def cancel(self, run_id):
        self.statuses[run_id] = "cancelled"
        return {"run": self.run(run_id)}


def task(task_id: str) -> dict:
    return {
        "schema_version": 3,
        "task_id": task_id,
        "project_id": "p",
        "design_id": "d",
        "plugin_id": "example",
        "inputs": {},
    }


def test_a_plan_runs_in_order_and_binds_a_predecessor_artifact(tmp_path: Path):
    store = PlanStore(tmp_path / "plans.sqlite")
    kernel = FakeKernel()
    executor = PlanExecutor(store, kernel)
    plan_id = store.create({
        "plan_id": "plan-1",
        "steps": [
            {"step_id": "produce", "task": task("produce")},
            {"step_id": "consume", "task": task("consume"), "bindings": [{
                "from_step": "produce", "artifact_kind": "report",
                "destination": "inputs/report.json",
            }]},
        ],
    })

    executor.cycle()
    assert [item["task_id"] for item in kernel.submitted] == ["produce"]

    kernel.statuses["run-1"] = "succeeded"
    kernel.outputs["run-1"] = [{
        "artifact_id": "artifact-1", "kind": "report", "sha256": "a" * 64,
        "metadata": {},
    }]
    executor.cycle()
    executor.cycle()

    assert [item["task_id"] for item in kernel.submitted] == ["produce", "consume"]
    assert kernel.submitted[1]["staged_inputs"] == [{
        "artifact_id": "artifact-1", "destination": "inputs/report.json",
        "required": True,
    }]

    kernel.statuses["run-2"] = "succeeded"
    executor.cycle()
    assert store.get(plan_id)["status"] == "succeeded"


def test_store_closes_each_sqlite_connection(tmp_path: Path, monkeypatch):
    closed: list[sqlite3.Connection] = []
    journal_mode_calls: list[str] = []
    connect = sqlite3.connect

    class TrackedConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql == "PRAGMA journal_mode = DELETE":
                journal_mode_calls.append(sql)
            return super().execute(sql, *args, **kwargs)

        def close(self) -> None:
            closed.append(self)
            super().close()

    def tracked_connect(*args, **kwargs):
        return connect(*args, factory=TrackedConnection, **kwargs)

    monkeypatch.setattr(plan_executor.sqlite3, "connect", tracked_connect)
    store = PlanStore(tmp_path / "plans.sqlite")
    store.create({
        "plan_id": "plan-closed-connections",
        "steps": [{"step_id": "only", "task": task("only")}],
    })
    store.get("plan-closed-connections")

    assert len(closed) == 3
    assert len(journal_mode_calls) == 1


def test_plan_preserves_agent_capability_script_and_patch_inputs(tmp_path: Path):
    script = tmp_path / "place.tcl"
    patch = tmp_path / "candidate.patch"
    script.write_text("set place_density 0.72\n", encoding="utf-8")
    patch.write_text("diff --git a/src/heuristic.cc b/src/heuristic.cc\n",
                     encoding="utf-8")
    payload = task("agent-experiment")
    payload["inputs"] = {
        "capability": "script_execution",
        "script_path": "inputs/place.tcl",
        "patch_path": "inputs/candidate.patch",
    }
    payload["parameters"] = {"density": 0.72, "strategy": "timing_driven"}
    payload["plugin_version"] = "2.0.0"
    payload["staged_inputs"] = [
        {"source": str(script), "destination": "inputs/place.tcl"},
        {"source": str(patch), "destination": "inputs/candidate.patch"},
    ]
    store = PlanStore(tmp_path / "plans.sqlite")
    kernel = FakeKernel()
    executor = PlanExecutor(store, kernel)
    store.create({
        "plan_id": "agent-authored-experiment",
        "steps": [{"step_id": "experiment", "task": payload}],
    })

    executor.cycle()

    assert kernel.submitted == [payload]
    assert kernel.submitted[0]["plugin_version"] == "2.0.0"


def test_a_failed_step_stops_the_plan_and_names_the_failure_source(tmp_path: Path):
    store = PlanStore(tmp_path / "plans.sqlite")
    kernel = FakeKernel()
    executor = PlanExecutor(store, kernel)
    plan_id = store.create({
        "plan_id": "plan-fails",
        "steps": [
            {"step_id": "bad", "task": task("bad")},
            {"step_id": "never", "task": task("never")},
        ],
    })

    executor.cycle()
    kernel.statuses["run-1"] = "failed"
    kernel.failures["run-1"] = {
        "category": "tool_error", "message": "tool refused the design",
        "retryable": False,
    }
    executor.cycle()

    plan = store.get(plan_id)
    assert plan["status"] == "failed"
    assert plan["failure"]["source"] == "plugin"
    assert plan["failure"]["category"] == "tool_error"
    assert len(kernel.submitted) == 1


def test_a_binding_can_only_refer_to_an_earlier_step(tmp_path: Path):
    store = PlanStore(tmp_path / "plans.sqlite")
    with pytest.raises(PlanError, match="earlier step"):
        store.create({
            "plan_id": "plan-forward-reference",
            "steps": [
                {"step_id": "first", "task": task("first"), "bindings": [{
                    "from_step": "later", "artifact_kind": "report",
                    "destination": "input.json",
                }]},
                {"step_id": "later", "task": task("later")},
            ],
        })


def test_the_http_api_accepts_and_returns_a_plan(tmp_path: Path):
    store = PlanStore(tmp_path / "plans.sqlite")
    kernel = FakeKernel()
    executor = PlanExecutor(store, kernel)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), build_handler(store, executor, kernel)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        status, created = http("POST", f"{base}/plans", {
            "plan_id": "plan-http",
            "steps": [{"step_id": "only", "task": task("only")}],
        })
        assert status == 201
        assert created["plan"]["status"] == "queued"

        executor.cycle()
        status, detail = http("GET", f"{base}/plans/plan-http")
        assert status == 200
        assert detail["plan"]["steps"][0]["run_id"] == "run-1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_cancelling_a_plan_cancels_the_active_run_and_never_starts_the_next(
    tmp_path: Path,
):
    store = PlanStore(tmp_path / "plans.sqlite")
    kernel = FakeKernel()
    executor = PlanExecutor(store, kernel)
    plan_id = store.create({
        "plan_id": "plan-cancel",
        "steps": [
            {"step_id": "active", "task": task("active")},
            {"step_id": "never", "task": task("never")},
        ],
    })
    executor.cycle()

    store.request_cancel(plan_id)
    executor.cycle()

    plan = store.get(plan_id)
    assert plan["status"] == "cancelled"
    assert kernel.statuses["run-1"] == "cancelled"
    assert len(kernel.submitted) == 1


def test_cancelling_before_submission_marks_every_step_cancelled(tmp_path: Path):
    store = PlanStore(tmp_path / "plans.sqlite")
    kernel = FakeKernel()
    executor = PlanExecutor(store, kernel)
    plan_id = store.create({
        "plan_id": "plan-cancel-before-submit",
        "steps": [
            {"step_id": "first", "task": task("first")},
            {"step_id": "second", "task": task("second")},
        ],
    })

    store.request_cancel(plan_id)
    executor.cycle()

    plan = store.get(plan_id)
    assert plan["status"] == "cancelled"
    assert [step["status"] for step in plan["steps"]] == [
        "cancelled", "cancelled"
    ]
    assert len(kernel.submitted) == 0


def test_submission_after_cancel_keeps_the_cancel_request(tmp_path: Path):
    store = PlanStore(tmp_path / "plans.sqlite")
    plan_id = store.create({
        "plan_id": "plan-cancel-in-flight",
        "steps": [{"step_id": "only", "task": task("only")}],
    })

    store.request_cancel(plan_id)
    store.submitted(plan_id, "only", "run-1")

    plan = store.get(plan_id)
    assert plan["status"] == "cancel_requested"
    assert plan["steps"][0]["run_id"] == "run-1"


def test_a_kernel_refusal_becomes_a_terminal_plan_failure(tmp_path: Path):
    class RefusingKernel(FakeKernel):
        def submit(self, task, *, idempotent=False, idempotency_key=None):
            raise KernelError("unknown plugin", status=400)

    store = PlanStore(tmp_path / "plans.sqlite")
    plan_id = store.create({
        "plan_id": "plan-refused",
        "steps": [{"step_id": "bad", "task": task("bad")}],
    })

    PlanExecutor(store, RefusingKernel()).cycle()

    plan = store.get(plan_id)
    assert plan["status"] == "failed"
    assert plan["failure"]["source"] == "platform"
    assert plan["failure"]["category"] == "submission_refused"
    assert "unknown plugin" in plan["failure"]["message"]


def test_a_temporarily_unavailable_kernel_keeps_the_plan_retryable(
    tmp_path: Path,
):
    class RecoveringKernel(FakeKernel):
        available = False

        def submit(self, task, *, idempotent=False, idempotency_key=None):
            if not self.available:
                raise KernelUnavailable("gateway is restarting")
            return super().submit(
                task, idempotent=idempotent, idempotency_key=idempotency_key
            )

    store = PlanStore(tmp_path / "plans.sqlite")
    kernel = RecoveringKernel()
    executor = PlanExecutor(store, kernel, kernel_retry_delay=0)
    plan_id = store.create({
        "plan_id": "plan-kernel-restarts",
        "steps": [{"step_id": "only", "task": task("only")}],
    })

    executor.cycle()

    waiting = store.get(plan_id)
    assert waiting["status"] == "retry_wait"
    assert waiting["failure"] == {
        "source": "platform",
        "category": "kernel_unavailable",
        "message": "gateway is restarting",
        "retryable": True,
    }

    kernel.available = True
    executor.cycle()

    assert store.get(plan_id)["status"] == "running"
    assert kernel.submitted == [task("only")]


def test_a_plan_does_not_claim_cancellation_before_the_kernel_settles(
    tmp_path: Path,
):
    class DelayedCancelKernel(FakeKernel):
        def cancel(self, run_id):
            self.statuses[run_id] = "cancel_requested"
            return {"run": self.run(run_id)}

    store = PlanStore(tmp_path / "plans.sqlite")
    kernel = DelayedCancelKernel()
    executor = PlanExecutor(store, kernel)
    plan_id = store.create({
        "plan_id": "plan-delayed-cancel",
        "steps": [{"step_id": "active", "task": task("active")}],
    })
    executor.cycle()
    store.request_cancel(plan_id)

    executor.cycle()

    plan = store.get(plan_id)
    assert plan["status"] == "cancel_requested"
    assert plan["steps"][0]["status"] == "cancel_requested"


def test_cancellation_race_preserves_kernel_success(tmp_path: Path):
    class CompletedOnCancelKernel(FakeKernel):
        def cancel(self, run_id):
            self.statuses[run_id] = "succeeded"
            return {"run": self.run(run_id)}

    store = PlanStore(tmp_path / "plans.sqlite")
    kernel = CompletedOnCancelKernel()
    executor = PlanExecutor(store, kernel)
    plan_id = store.create({
        "plan_id": "plan-success-race",
        "steps": [{"step_id": "active", "task": task("active")}],
    })
    executor.cycle()
    store.request_cancel(plan_id)

    executor.cycle()

    assert store.get(plan_id)["status"] == "succeeded"


def test_cancellation_race_does_not_skip_queued_steps(tmp_path: Path):
    class CompletedOnCancelKernel(FakeKernel):
        def cancel(self, run_id):
            self.statuses[run_id] = "succeeded"
            return {"run": self.run(run_id)}

    store = PlanStore(tmp_path / "plans.sqlite")
    kernel = CompletedOnCancelKernel()
    executor = PlanExecutor(store, kernel)
    plan_id = store.create({
        "plan_id": "plan-partial-success-race",
        "steps": [
            {"step_id": "active", "task": task("active")},
            {"step_id": "never", "task": task("never")},
        ],
    })
    executor.cycle()
    store.request_cancel(plan_id)

    executor.cycle()

    plan = store.get(plan_id)
    assert plan["status"] == "cancelled"
    assert plan["execution_valid"] is False
    assert plan["steps"][0]["status"] == "succeeded"
    assert plan["steps"][1]["status"] == "cancelled"
    assert len(kernel.submitted) == 1


def test_plan_submission_recovery_reuses_the_same_kernel_run(tmp_path: Path):
    class CrashAfterSubmitStore(PlanStore):
        failed_once = False

        def submitted(self, plan_id, step_id, run_id):
            if not self.failed_once:
                self.failed_once = True
                raise RuntimeError("simulated executor crash")
            super().submitted(plan_id, step_id, run_id)

    store = CrashAfterSubmitStore(tmp_path / "plans.sqlite")
    kernel = FakeKernel()
    plan_id = store.create({
        "plan_id": "plan-recovery",
        "steps": [{"step_id": "only", "task": task("same-task")}],
    })
    with pytest.raises(RuntimeError, match="simulated executor crash"):
        PlanExecutor(store, kernel).cycle()

    PlanExecutor(store, kernel).cycle()

    plan = store.get(plan_id)
    assert plan["steps"][0]["run_id"] == "run-1"
    assert len(kernel.submitted) == 1


def http(method: str, url: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())
