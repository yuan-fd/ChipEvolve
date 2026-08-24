from __future__ import annotations

import sys
from pathlib import Path

from openroad_platform_contracts import RuntimeStatus
from openroad_platform_execution import (
    PluginRegistry, build_rtl_verify_task, rtl_verify_plugin_manifest,
)
from openroad_platform_scheduler import RuntimeStore, WorkflowRuntime


def _tool(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\necho fake $0\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_rtl_verify_plugin_runs_in_runtime_and_hashes_artifacts(tmp_path):
    rtl = tmp_path / "adder.sv"
    rtl.write_text("module adder(input a, input b, output y); assign y=a+b; endmodule\n")
    manifest = rtl_verify_plugin_manifest(
        verilator_bin=_tool(tmp_path / "bin/verilator"), yosys_bin=_tool(tmp_path / "bin/yosys"),
        python_executable=sys.executable,
    )
    runtime = WorkflowRuntime(RuntimeStore(tmp_path / "runtime.db"), PluginRegistry([manifest]),
                              workspace_root=tmp_path / "runs")
    task = build_rtl_verify_task(project_id="v2", design_id="adder", rtl_path=rtl,
                                 top="adder", spec_id="spec-adder", verification_id="verify-adder")
    run = runtime.submit(task, capability="eda.rtl.verify")
    completed = runtime.execute_once(run.run_id)
    assert completed.status is RuntimeStatus.SUCCEEDED
    artifacts = runtime.describe(run.run_id)["stages"][0]["attempts"][0]["artifacts"]
    assert {item["kind"] for item in artifacts} == {"rtl", "verification_report", "log"}
