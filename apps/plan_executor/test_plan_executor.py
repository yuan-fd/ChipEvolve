"""The execution-plan lifecycle, independent of any concrete EDA plugin."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from openroad_app_plan_executor.__main__ import (
    PlanError,
    PlanExecutor,
    PlanStore,
    build_handler,
)
from openroad_platform_client import KernelError


class FakeKernel:
    """The smallest real state machine the plan service needs from the kernel."""

    def __init__(self) -> None:
        self.submitted: list[dict] = []
        self.statuses: dict[str, str] = {}
        self.outputs: dict[str, list[dict]] = {}
        self.failures: dict[str, dict] = {}

    def submit(self, task, *, idempotent=False):
        self.submitted.append(dict(task))
        run_id = f"run-{len(self.submitted)}"
        self.statuses[run_id] = "queued"
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


def test_a_kernel_refusal_becomes_a_terminal_plan_failure(tmp_path: Path):
    class RefusingKernel(FakeKernel):
        def submit(self, task, *, idempotent=False):
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
