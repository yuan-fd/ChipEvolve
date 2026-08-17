"""Auto-learning unit tests.

Succeeded terminal runs must be collected into the knowledge base; failed /
cancelled / timed-out runs must be recorded as *rejections* (audit trail only)
and must never appear in the learning observations.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "packages/contracts/src", ROOT / "packages/scheduler/src",
               ROOT / "packages/analysis/src", ROOT / "apps"):
    sys.path.insert(0, str(source))

from openroad_platform_contracts import (  # noqa: E402
    LearningContext, RuntimeStatus, TaskSpec,
)
from openroad_platform_scheduler import (  # noqa: E402
    RuntimeAttempt, RuntimeRun, RuntimeStageRun,
)
from openroad_platform_analysis import (  # noqa: E402
    LearningCollector, TenantLearningStore,
)

SHA = "a" * 64


def _task(owner: str = "user-test") -> TaskSpec:
    return TaskSpec(
        task_id="task-auto-1", project_id="project-p0", design_id="gcd",
        plugin_id="orfs",
        inputs={"rtl": {"path": "design.v", "size_bytes": 12, "sha256": SHA}},
        parameters={"platform": "asap7"},
        labels={"owner_id": owner},
        timeout_seconds=60,
    )


def _run(run_id: str, status: RuntimeStatus, task: TaskSpec) -> RuntimeRun:
    return RuntimeRun(run_id=run_id, task_id=task.task_id, status=status,
                      task_spec=task, created_at="2026-08-17T00:00:00",
                      started_at=None, ended_at=None, terminal_reason=None)


def _stage(run_id: str) -> RuntimeStageRun:
    return RuntimeStageRun(stage_run_id="stage-1", run_id=run_id,
                           stage_key="finish", ordinal=1, plugin_id="orfs",
                           plugin_version="1.0.0", status=RuntimeStatus.SUCCEEDED,
                           successful_attempt_id="attempt-1",
                           created_at="2026-08-17T00:00:00",
                           started_at=None, ended_at=None)


def _attempt(status: RuntimeStatus, failure=None) -> RuntimeAttempt:
    return RuntimeAttempt(attempt_id="attempt-1", stage_run_id="stage-1",
                          attempt_number=1, status=status, workspace="/tmp/w",
                          worker_id="w", lease_expires_at=None, heartbeat_at=None,
                          started_at="2026-08-17T00:00:00", ended_at=None,
                          exit_code=1 if status is RuntimeStatus.FAILED else 0,
                          failure=failure)


class FakeRuntimeStore:
    def __init__(self, run: RuntimeRun, stage: RuntimeStageRun,
                 attempt: RuntimeAttempt):
        self._run, self._stage, self._attempt = run, stage, attempt

    def get_run(self, run_id):  # noqa: D102
        return self._run

    def list_stages(self, run_id):  # noqa: D102
        return [self._stage]

    def list_attempts(self, stage_run_id):  # noqa: D102
        return [self._attempt]

    def metrics(self, attempt_id):  # noqa: D102
        return []

    def artifacts(self, attempt_id):  # noqa: D102
        return []


def _context() -> LearningContext:
    return LearningContext(design_id="gcd", design_fingerprint=SHA, platform="asap7",
                           pdk_id="asap7", toolchain_id="orfs-1.0.0",
                           flow_stage="finish", metric_parser_version="unit-v1")


def test_reject_records_rejection_and_keeps_observations_empty(tmp_path):
    store = TenantLearningStore(tmp_path / "tenant-learning.db")
    task = _task()
    run = _run("run-fail", RuntimeStatus.FAILED, task)
    attempt = _attempt(RuntimeStatus.FAILED, failure={"category": "upstream_failure",
                                                      "message": "tool crashed"})
    collector = LearningCollector(FakeRuntimeStore(run, _stage("run-fail"), attempt),
                                  store)
    rid = collector.reject("run-fail", _context(), tenant_id="user-test",
                           project_id="project-p0", run_status="failed",
                           reason="tool crashed")
    assert rid.startswith("reject-")
    assert store.list("user-test", "project-p0") == []
    rejections = store.rejections("user-test", "project-p0")
    assert len(rejections) == 1
    assert rejections[0]["run_status"] == "failed"
    assert "tool crashed" in rejections[0]["reason"]


def test_reject_is_idempotent(tmp_path):
    store = TenantLearningStore(tmp_path / "tenant-learning.db")
    task = _task()
    run = _run("run-fail2", RuntimeStatus.CANCELLED, task)
    attempt = _attempt(RuntimeStatus.CANCELLED)
    collector = LearningCollector(FakeRuntimeStore(run, _stage("run-fail2"), attempt),
                                  store)
    for _ in range(2):
        collector.reject("run-fail2", _context(), tenant_id="user-test",
                         project_id="project-p0", run_status="cancelled",
                         reason="user cancelled")
    assert len(store.rejections("user-test", "project-p0")) == 1


def test_auto_routing_collects_succeeded_and_rejects_failed(tmp_path):
    """Exercise the real ApiState.auto_collect_terminal_run routing logic."""
    from apps.api.app import ApiState  # noqa: E402

    store = TenantLearningStore(tmp_path / "tenant-learning.db")

    # succeeded run -> routed to collect (receipt may be rejected on fake data,
    # but the action must be "collect")
    task_ok = _task(owner="user-test")
    run_ok = _run("run-ok", RuntimeStatus.SUCCEEDED, task_ok)
    attempt_ok = _attempt(RuntimeStatus.SUCCEEDED)
    fake_ok = FakeRuntimeStore(run_ok, _stage("run-ok"), attempt_ok)
    state_ok = ApiState.__new__(ApiState)
    state_ok.runtime_store = fake_ok
    state_ok.learning_collector = LearningCollector(fake_ok, store)
    result_ok = state_ok.auto_collect_terminal_run("run-ok")
    assert result_ok["action"] == "collect"
    assert result_ok["status"] in {"admitted", "rejected", "quarantined"}

    # failed run -> routed to reject, rejection recorded, observations stay clean
    task_bad = _task(owner="user-test")
    run_bad = _run("run-bad", RuntimeStatus.FAILED, task_bad)
    attempt_bad = _attempt(RuntimeStatus.FAILED,
                           failure={"category": "artifact_missing", "message": "no gds"})
    fake_bad = FakeRuntimeStore(run_bad, _stage("run-bad"), attempt_bad)
    state_bad = ApiState.__new__(ApiState)
    state_bad.runtime_store = fake_bad
    state_bad.learning_collector = LearningCollector(fake_bad, store)
    result_bad = state_bad.auto_collect_terminal_run("run-bad")
    assert result_bad["action"] == "reject"
    assert result_bad["rejection_id"].startswith("reject-")
    assert store.list("user-test", "project-p0") == []
    rejections = store.rejections("user-test", "project-p0")
    assert any(row["run_id"] == "run-bad" for row in rejections)


def test_auto_routing_skips_non_terminal(tmp_path):
    from apps.api.app import ApiState  # noqa: E402

    store = TenantLearningStore(tmp_path / "tenant-learning.db")
    task = _task()
    run = _run("run-pending", RuntimeStatus.QUEUED, task)
    attempt = _attempt(RuntimeStatus.QUEUED)
    fake = FakeRuntimeStore(run, _stage("run-pending"), attempt)
    state = ApiState.__new__(ApiState)
    state.runtime_store = fake
    state.learning_collector = LearningCollector(fake, store)
    result = state.auto_collect_terminal_run("run-pending")
    assert result["action"] == "skipped"
    assert store.list("user-test", "project-p0") == []
    assert store.rejections("user-test", "project-p0") == []
