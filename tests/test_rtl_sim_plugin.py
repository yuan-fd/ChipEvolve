from __future__ import annotations

import sys
from pathlib import Path

from openroad_platform_contracts import RuntimeStatus
from openroad_platform_execution import PluginRegistry, build_rtl_sim_task, rtl_sim_plugin_manifest
from openroad_platform_scheduler import RuntimeStore, WorkflowRuntime


def _tool(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8"); path.chmod(0o755); return path


def test_rtl_simulation_uses_hashed_frozen_testbench(tmp_path):
    rtl = tmp_path / "dut.sv"; rtl.write_text("module dut(input a, output y); assign y=a; endmodule\n")
    tb = tmp_path / "tb.sv"; tb.write_text("module tb; endmodule\n")
    compiler = _tool(tmp_path / "bin" / "iverilog", "#!/bin/sh\nwhile [ \"$1\" != \"-o\" ]; do shift; done\nshift\nprintf '#!/bin/sh\\necho \"TB_SUMMARY total=1 errors=0\"\\necho PASS\\nexit 0\\n' > \"$1\"\nchmod +x \"$1\"\n")
    runner = _tool(tmp_path / "bin" / "vvp", "#!/bin/sh\n\"$1\"\n")
    manifest = rtl_sim_plugin_manifest(iverilog_bin=compiler, vvp_bin=runner, python_executable=sys.executable)
    runtime = WorkflowRuntime(RuntimeStore(tmp_path / "runtime.db"), PluginRegistry([manifest]), workspace_root=tmp_path / "runs")
    task = build_rtl_sim_task(project_id="v2", design_id="dut", rtl_path=rtl, testbench_path=tb,
                              top="tb", spec_id="spec-dut", verification_id="verify-dut")
    run = runtime.submit(task, capability="eda.rtl.simulate")
    assert runtime.execute_once(run.run_id).status is RuntimeStatus.SUCCEEDED
    artifacts = runtime.describe(run.run_id)["stages"][0]["attempts"][0]["artifacts"]
    assert {item["kind"] for item in artifacts} == {"simulation_report", "log"}
    tb.write_text("module tb; initial $finish; endmodule\n")
    second = runtime.submit(task, capability="eda.rtl.simulate")
    assert runtime.execute_once(second.run_id).status is RuntimeStatus.FAILED


def test_rtl_simulation_rejects_zero_exit_with_failed_summary(tmp_path):
    rtl = tmp_path / "dut.sv"; rtl.write_text("module dut; endmodule\n")
    tb = tmp_path / "tb.sv"; tb.write_text("module tb; endmodule\n")
    compiler = _tool(tmp_path / "bin" / "iverilog", "#!/bin/sh\nwhile [ \"$1\" != \"-o\" ]; do shift; done\nshift\nprintf '#!/bin/sh\\necho \"TB_SUMMARY total=2 errors=1\"\\nexit 0\\n' > \"$1\"\nchmod +x \"$1\"\n")
    runner = _tool(tmp_path / "bin" / "vvp", "#!/bin/sh\n\"$1\"\n")
    runtime = WorkflowRuntime(RuntimeStore(tmp_path / "runtime.db"), PluginRegistry([
        rtl_sim_plugin_manifest(iverilog_bin=compiler, vvp_bin=runner,
                                python_executable=sys.executable)
    ]), workspace_root=tmp_path / "runs")
    task = build_rtl_sim_task(project_id="v2", design_id="dut", rtl_path=rtl,
                              testbench_path=tb, top="tb", spec_id="spec-dut",
                              verification_id="verify-dut")
    assert runtime.execute_once(runtime.submit(task).run_id).status is RuntimeStatus.FAILED
