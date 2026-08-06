from __future__ import annotations

from datetime import datetime, timezone

import pytest

from openroad_platform_analysis import LearningCollector, TenantLearningStore
from openroad_platform_contracts import LearningContext, RuntimeStatus, TaskSpec
from openroad_platform_scheduler import RuntimeStore


def test_collector_quarantines_verifies_admits_idempotently_and_isolates_tenants(tmp_path):
    runtime = RuntimeStore(tmp_path / "runtime.db")
    task = TaskSpec("collector-task", "project-a", "gcd", "orfs",
                    inputs={"rtl": {"sha256": "a" * 64}},
                    parameters={"platform": "nangate45", "place_density": .5})
    run, stage = runtime.submit_plugin_run(task, plugin_version="1.0.0")
    attempt = runtime.start_attempt(stage.stage_run_id, worker_id="test",
                                    workspace=tmp_path / "workspace", lease_seconds=10)
    artifact = runtime.register_artifact(attempt.attempt_id, kind="run_result",
                                         store_key="result.json", size_bytes=1,
                                         sha256="b" * 64)
    runtime.register_metrics(attempt.attempt_id, [{"name": "area_um2", "value": 123,
        "unit": "um2", "source_artifact_id": artifact, "parser_id": "fixture",
        "parser_version": "1", "context": {"source": "observed"}}])
    runtime.finish_attempt(attempt.attempt_id, RuntimeStatus.SUCCEEDED, exit_code=0,
                           now=datetime.now(timezone.utc))
    context = LearningContext("gcd", "a" * 64, "nangate45", "nangate45-public",
                              "orfs-fixed", "finish", "parser-1")
    learning = TenantLearningStore(tmp_path / "learning.db")
    collector = LearningCollector(runtime, learning)
    first = collector.collect(run.run_id, context, tenant_id="alice", project_id="project-a")
    second = collector.collect(run.run_id, context, tenant_id="alice", project_id="project-a")
    assert first == second and first.status == "admitted"
    assert len(learning.list("alice", "project-a")) == 1
    assert learning.list("bob", "project-a") == []
    learning.set_shared_opt_in("alice", "project-a", first.observation_id, True)
    assert len(learning.shared()) == 1


def test_collector_rejects_context_tampering_without_affecting_runtime(tmp_path):
    runtime = RuntimeStore(tmp_path / "runtime.db")
    task = TaskSpec("collector-bad", "project-a", "gcd", "orfs",
                    inputs={"rtl": {"sha256": "a" * 64}},
                    parameters={"platform": "nangate45"})
    run, stage = runtime.submit_plugin_run(task, plugin_version="1.0.0")
    attempt = runtime.start_attempt(stage.stage_run_id, worker_id="test",
                                    workspace=tmp_path / "workspace", lease_seconds=10)
    runtime.finish_attempt(attempt.attempt_id, RuntimeStatus.FAILED, exit_code=1,
                           failure={"category": "tool_error"}, now=datetime.now(timezone.utc))
    wrong = LearningContext("gcd", "c" * 64, "nangate45", "nangate45-public",
                            "orfs-fixed", "finish", "parser-1")
    receipt = LearningCollector(runtime, TenantLearningStore(tmp_path / "learning.db")).collect(
        run.run_id, wrong, tenant_id="alice", project_id="project-a")
    assert receipt.status == "rejected"
    assert "fingerprint" in receipt.reason
    assert runtime.get_run(run.run_id).status == RuntimeStatus.FAILED

