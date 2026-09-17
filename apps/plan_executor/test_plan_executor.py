"""The execution-plan lifecycle, independent of any concrete EDA plugin."""

from __future__ import annotations

from pathlib import Path

import pytest
from openroad_app_plan_executor.__main__ import (
    PlanError,
    PlanExecutor,
    PlanStore,
)


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
