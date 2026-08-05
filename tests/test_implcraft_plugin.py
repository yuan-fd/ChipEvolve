from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path

import pytest

from openroad_platform_contracts import RuntimeStatus
from openroad_platform_execution import (
    PluginRegistry, build_implcraft_task, implcraft_plugin_manifest,
)
from openroad_platform_scheduler import RuntimeStore, WorkflowRuntime


FAKE_RUNNER = '''
import argparse,json
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument("--config"); p.add_argument("--work-root"); p.add_argument("--dry-run",action="store_true"); p.add_argument("--stop-at"); a=p.parse_args()
assert a.dry_run and a.stop_at == "floorplan"
root=Path(a.work_root); script=root/"synthesis"/"DC"/"script"/"run.tcl"; script.parent.mkdir(parents=True); script.write_text("read_verilog design.v\\ncompile_ultra\\n")
(root/"design_state.json").write_text(json.dumps({"stage_results":{"synthesis":{"status":"passed"}}}))
(root/"qor_report.txt").write_text("ImplCraft dry-run report\\n")
'''


def _repo(path: Path) -> str:
    runner = path / "ImplCraft/src/run_flow.py"
    runner.parent.mkdir(parents=True)
    runner.write_text(FAKE_RUNNER, encoding="utf-8")
    (runner.parent / "__init__.py").write_text("", encoding="utf-8")
    (path / "ImplCraft/src/__init__.py").write_text("", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run([
        "git", "-C", str(path), "-c", "user.name=Test", "-c",
        "user.email=test@example.invalid", "commit", "-qm", "fixture",
    ], check=True)
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()


def test_implcraft_task_is_explicitly_script_generation_only(tmp_path):
    rtl = tmp_path / "top.v"
    rtl.write_text("module top(input clk); endmodule\n", encoding="utf-8")
    task = build_implcraft_task(
        rtl, project_id="p11", design_id="top", top="top", stop_at="floorplan",
    )
    assert task.parameters["mode"] == "dry-run"
    assert task.labels["commercial_eda_executed"] == "false"
    with pytest.raises(ValueError, match="stop_at"):
        build_implcraft_task(
            rtl, project_id="p11", design_id="top", top="top", stop_at="finish",
        )


def test_implcraft_commit_mismatch_fails_closed(tmp_path):
    source = tmp_path / "edacraft"
    _repo(source)
    with pytest.raises(ValueError, match="commit mismatch"):
        implcraft_plugin_manifest(source, sys.executable, expected_commit="0" * 40)


def test_implcraft_dry_run_is_registered_by_runtime(tmp_path):
    source = tmp_path / "edacraft"
    commit = _repo(source)
    rtl = tmp_path / "top.v"
    rtl.write_text("module top(input clk); endmodule\n", encoding="utf-8")
    manifest = implcraft_plugin_manifest(
        source, sys.executable, expected_commit=commit, default_timeout_seconds=30,
    )
    runtime = WorkflowRuntime(
        RuntimeStore(tmp_path / "runtime.db"), PluginRegistry([manifest]),
        workspace_root=tmp_path / "runs", worker_id="p11-fixture",
    )
    run = runtime.submit(build_implcraft_task(
        rtl, project_id="p11", design_id="top", top="top", timeout_seconds=30,
    ))
    completed = runtime.execute_once(run.run_id)
    attempt = runtime.describe(run.run_id)["stages"][0]["attempts"][0]
    assert completed.status is RuntimeStatus.SUCCEEDED
    kinds = {item["kind"] for item in attempt["artifacts"]}
    assert kinds == {
        "implcraft_config", "implcraft_state", "eda_script", "report",
        "toolchain_snapshot", "log",
    }
    snapshot = next(item for item in attempt["artifacts"]
                    if item["kind"] == "toolchain_snapshot")
    payload = json.loads(
        (Path(attempt["workspace"]) / snapshot["store_key"]).read_text(encoding="utf-8")
    )
    assert payload["commercial_eda_executed"] is False


def test_repository_implcraft_manifest_is_contract_valid():
    root = Path(__file__).parents[1]
    registry = PluginRegistry.from_directory(root / "integrations/edacraft_implcraft")
    manifest = registry.resolve(
        "edacraft-implcraft", version="1.0.0",
        capability="eda.implcraft.scriptgen", arch=platform.machine(),
    )
    assert manifest.environment["IMPLCRAFT_COMMERCIAL_EDA_EXECUTED"] == "false"
