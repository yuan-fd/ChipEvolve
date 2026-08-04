from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from openroad_platform_contracts import ExperimentPlan, RuntimeStatus
from openroad_platform_execution import agenticpd_plugin_manifest, build_agenticpd_task
from openroad_platform_execution.agenticpd_adapter import _candidate
from openroad_platform_execution.registry import PluginRegistry
from openroad_platform_scheduler import RuntimeStore, WorkflowRuntime


def test_agenticpd_task_never_persists_credential():
    task = build_agenticpd_task(project_id="p5", design_id="adder", mode="real")
    assert "DEEPSEEK_API_KEY" not in json.dumps(task.to_dict()).replace(
        '"credential_env": "DEEPSEEK_API_KEY"', ""
    )
    assert task.resources["credential_env"] == "DEEPSEEK_API_KEY"


def test_candidate_only_activates_unambiguous_orfs_parameter():
    candidate = _candidate({
        "trial_id": "abc12345",
        "params": {"FP": {"CORE_UTILIZATION": 35, "CORE_ASPECT_RATIO": 0.85},
                   "PL": {"PLACE_DENSITY_LB_ADDON": 0.08}},
    })
    assert candidate.parameters == {"core_utilization_pct": 35.0}
    assert candidate.unsupported_parameters == {
        "FP.CORE_ASPECT_RATIO": 0.85, "PL.PLACE_DENSITY_LB_ADDON": 0.08,
    }
    plan = ExperimentPlan(
        plan_id="p", producer="agenticpd", design_id="adder", platform="nangate45",
        baseline_parameters={"core_utilization_pct": 38.0}, candidates=(candidate,),
        max_child_runs=1,
    )
    assert ExperimentPlan.from_dict(plan.to_dict()) == plan


def test_real_mode_without_credential_fails_closed(tmp_path):
    source = tmp_path / "agenticpd"
    source.mkdir()
    (source / "main.py").write_text("raise SystemExit('must not run')\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "-qm", "fixture"], check=True)
    commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    manifest = agenticpd_plugin_manifest(source, python_executable=sys.executable,
                                         expected_commit=commit, default_timeout_seconds=10)
    runtime = WorkflowRuntime(RuntimeStore(tmp_path / "runtime.db"),
                              PluginRegistry([manifest]), workspace_root=tmp_path / "runs")
    run = runtime.submit(build_agenticpd_task(project_id="p5", design_id="adder",
                                               mode="real", timeout_seconds=10))
    completed = runtime.execute_once(run.run_id)
    attempt = runtime.describe(run.run_id)["stages"][0]["attempts"][0]
    assert completed.status is RuntimeStatus.FAILED
    assert attempt["failure"]["category"] == "credential_unavailable"
