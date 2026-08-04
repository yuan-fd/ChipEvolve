from __future__ import annotations

import platform
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openroad_platform_contracts import PluginManifest, TaskSpec
from openroad_platform_execution import PluginRegistry
from openroad_platform_scheduler import (
    CampaignManager, CampaignStore, RuntimeStore, WorkflowRuntime,
)

FIXTURES = Path(__file__).parent / "fixtures"


def make_runtime(tmp_path: Path) -> WorkflowRuntime:
    manifest = PluginManifest(
        plugin_id="delay", plugin_version="1.0.0",
        adapter_entry=(sys.executable, str(FIXTURES / "delay_adapter.py")),
        capabilities=("test.delay",), supported_arch=(platform.machine(),),
        input_schema={"type": "object"}, output_schema={"type": "object"},
        artifact_rules=({"kind": "report", "required": True},),
        default_timeout_seconds=10,
    )
    return WorkflowRuntime(RuntimeStore(tmp_path / "runtime.db"),
                           PluginRegistry([manifest]),
                           workspace_root=tmp_path / "workspaces", lease_seconds=1)


def make_task(number: int, *, attempts: int = 1, delay: float = 0.1) -> TaskSpec:
    return TaskSpec(task_id=f"campaign-task-{number}", project_id="p6",
                    design_id=f"design-{number}", plugin_id="delay",
                    parameters={"delay": delay}, expected_artifacts=("report",),
                    timeout_seconds=10, max_attempts=attempts)


def test_campaign_is_durable_bounded_and_workspace_isolated(tmp_path):
    runtime = make_runtime(tmp_path)
    store = CampaignStore(tmp_path / "campaign.db")
    campaign_id = store.create("bounded", [make_task(1, delay=0.5),
                                            make_task(2, delay=0.5)], max_parallel=2)
    manager = CampaignManager(store, runtime)
    started = time.monotonic()
    view = manager.run_until_terminal(campaign_id, timeout_seconds=5)
    elapsed = time.monotonic() - started

    assert view["counts"] == {"succeeded": 2}
    assert elapsed < 0.85, "two 500ms adapters should run under a parallelism limit of two"
    workspaces = {
        runtime.store.list_attempts(runtime.store.list_stages(item["run_id"])[0].stage_run_id)[0].workspace
        for item in view["members"]
    }
    assert len(workspaces) == 2
    assert all(Path(path).is_relative_to(tmp_path / "workspaces") for path in workspaces)


def test_campaign_reopen_rebinds_by_task_id_without_duplicate_run(tmp_path):
    runtime = make_runtime(tmp_path)
    store = CampaignStore(tmp_path / "campaign.db")
    campaign_id = store.create("recover", [make_task(1)], max_parallel=1)
    manager = CampaignManager(store, runtime)
    first = manager.ensure_runs(campaign_id)

    reopened = CampaignManager(CampaignStore(tmp_path / "campaign.db"), make_runtime(tmp_path))
    second = reopened.ensure_runs(campaign_id)
    assert first == second
    assert len(runtime.store.list_runs()) == 1


def test_expired_worker_lease_preserves_attempt_and_retries(tmp_path):
    runtime = make_runtime(tmp_path)
    store = CampaignStore(tmp_path / "campaign.db")
    campaign_id = store.create("lease-recovery", [make_task(1, attempts=2)], max_parallel=1)
    manager = CampaignManager(store, runtime)
    run_id = manager.ensure_runs(campaign_id)[0]
    stage = runtime.store.list_stages(run_id)[0]
    first = runtime.store.start_attempt(stage.stage_run_id, worker_id="dead-worker",
                                        workspace=tmp_path / "dead-attempt", lease_seconds=1)
    runtime.store.expire_leases(now=datetime.now(timezone.utc) + timedelta(seconds=2))

    view = manager.run_until_terminal(campaign_id, timeout_seconds=5)
    attempts = runtime.store.list_attempts(stage.stage_run_id)
    assert view["counts"] == {"succeeded": 1}
    assert [attempt.status.value for attempt in attempts] == ["lost", "succeeded"]
    assert attempts[0].attempt_id == first.attempt_id


def test_campaign_cancel_cancels_all_queued_members(tmp_path):
    runtime = make_runtime(tmp_path)
    store = CampaignStore(tmp_path / "campaign.db")
    campaign_id = store.create("cancel", [make_task(1), make_task(2)], max_parallel=1)
    manager = CampaignManager(store, runtime)
    view = manager.cancel(campaign_id)
    assert view["counts"] == {"cancelled": 2}
    assert view["status"] == "finished"
