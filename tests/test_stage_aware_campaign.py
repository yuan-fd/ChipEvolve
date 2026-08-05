from __future__ import annotations

import json
import sys
from pathlib import Path

from openroad_platform_contracts import PluginManifest, RuntimeStatus, TaskSpec
from openroad_platform_execution import PluginRegistry
from openroad_platform_scheduler import (
    CampaignStore,
    RuntimeStore,
    StageAwareCampaignManager,
    WorkflowRuntime,
)


ADAPTER = r'''import argparse, json, signal, sys, time
from datetime import datetime, timezone
from pathlib import Path

p=argparse.ArgumentParser(); p.add_argument("--request", type=Path); p.add_argument("--result", type=Path)
a=p.parse_args(); request=json.loads(a.request.read_text()); task=request["task"]
params=task.get("parameters", {}); util=float(params.get("core_utilization_pct", 10))
started=datetime.now(timezone.utc).isoformat()
print("[orfs-stage-start] synth", flush=True)
if params.get("slow_seconds"):
    time.sleep(float(params["slow_seconds"]))
if util > 50:
    print("[orfs-stage] synth failed 0.010s", flush=True)
    result={"schema_version":1,"status":"failed","exit_code":2,"started_at":started,
      "ended_at":datetime.now(timezone.utc).isoformat(),"metrics":[],"artifacts":[],
      "failure":{"category":"congestion","message":"routing congestion overflow"},"provenance":{}}
    code=2
else:
    print("[orfs-stage] synth succeeded 0.010s", flush=True)
    result={"schema_version":1,"status":"succeeded","exit_code":0,"started_at":started,
      "ended_at":datetime.now(timezone.utc).isoformat(),
      "metrics":[{"name":"score","value":util}],"artifacts":[],"provenance":{}}
    code=0
a.result.write_text(json.dumps(result)); sys.exit(code)
'''


def _manager(tmp_path: Path):
    adapter = tmp_path / "adapter.py"
    adapter.write_text(ADAPTER)
    manifest = PluginManifest(
        plugin_id="orfs", plugin_version="1.0.0",
        adapter_entry=(sys.executable, str(adapter)), capabilities=("eda.orfs",),
        supported_arch=(__import__("platform").machine(),), input_schema={"type": "object"},
        output_schema={"type": "object"}, default_timeout_seconds=10,
    )
    runtime = WorkflowRuntime(
        RuntimeStore(tmp_path / "runtime.db"), PluginRegistry([manifest]),
        workspace_root=tmp_path / "runs", lease_seconds=2,
    )
    return StageAwareCampaignManager(CampaignStore(tmp_path / "campaign.db"), runtime)


def _task(**parameters):
    return TaskSpec(
        task_id="base-task", project_id="p13", design_id="top", plugin_id="orfs",
        parameters={"core_utilization_pct": 10.0, **parameters},
        timeout_seconds=10,
    )


def test_grid_runs_concurrently_and_records_top_k(tmp_path):
    manager = _manager(tmp_path)
    campaign_id = manager.create_grid(
        "grid", _task(), {"core_utilization_pct": [20, 30, 40]},
        max_parallel=2, objective_metric="score", direction="min", top_k=2,
    )

    view = manager.run_until_terminal(campaign_id, timeout_seconds=10)

    assert view["status"] == "finished"
    assert view["counts"] == {"succeeded": 3}
    assert [item["value"] for item in view["ranking"]] == [20.0, 30.0]
    assert any(item["kind"] == "top_k" for item in view["decisions"])
    for member in view["members"]:
        events = manager.runtime.store.events(member["run_id"])
        assert {event["event_type"] for event in events} >= {
            "tool.stage.started", "tool.stage.finished"
        }


def test_terminal_congestion_creates_traceable_repair_child(tmp_path):
    manager = _manager(tmp_path)
    campaign_id = manager.create_grid(
        "repair", _task(), {"core_utilization_pct": [55]},
        max_repairs=1, objective_metric="score",
    )

    view = manager.run_until_terminal(campaign_id, timeout_seconds=10)

    assert len(view["members"]) == 2
    assert view["counts"] == {"failed": 1, "succeeded": 1}
    child = manager.store.members(campaign_id)[1]
    assert child.task_spec.parameters["core_utilization_pct"] == 50.0
    assert child.task_spec.labels["repair_depth"] == "1"
    assert any(item["kind"] == "repair_created" for item in view["decisions"])


def test_stage_wall_clock_policy_prunes_slow_run(tmp_path):
    manager = _manager(tmp_path)
    campaign_id = manager.create_grid(
        "prune", _task(slow_seconds=3), {"core_utilization_pct": [20]},
        stage_budgets={"synth": 0.1}, max_repairs=0,
    )

    view = manager.run_until_terminal(campaign_id, timeout_seconds=8)

    assert view["members"][0]["status"] == RuntimeStatus.CANCELLED.value
    decision = next(item for item in view["decisions"] if item["kind"] == "prune")
    assert decision["payload"]["tool_stage"] == "synth"
