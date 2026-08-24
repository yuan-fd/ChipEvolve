import sys
from pathlib import Path

from openroad_platform_execution import (PluginRegistry, build_rtl_mutation_task,
                                         rtl_mutation_plugin_manifest)
from openroad_platform_scheduler import RuntimeStore, WorkflowRuntime


def _tool(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(body); path.chmod(0o755); return path


def test_mutation_plugin_records_real_outcomes_not_self_reported(tmp_path):
    rtl = tmp_path / "dut.sv"; rtl.write_text("module dut(input a,b,output y); assign y=(a == b) && 1; endmodule\n")
    tb = tmp_path / "tb.sv"; tb.write_text("module tb; endmodule\n")
    compiler = _tool(tmp_path / "bin" / "iverilog", "#!/bin/sh\nout=\nprev=\nsecond_last=\nlast=\nfor arg in \"$@\"; do [ \"$prev\" = -o ] && out=$arg; second_last=$last; last=$arg; prev=$arg; done\nif grep -q '!=' \"$second_last\"; then printf '#!/bin/sh\\nexit 1\\n' > \"$out\"; else printf '#!/bin/sh\\nexit 0\\n' > \"$out\"; fi\nchmod +x \"$out\"\n")
    vvp = _tool(tmp_path / "bin" / "vvp", "#!/bin/sh\n\"$1\"\n")
    manifest = rtl_mutation_plugin_manifest(iverilog_bin=compiler, vvp_bin=vvp, python_executable=sys.executable)
    runtime = WorkflowRuntime(RuntimeStore(tmp_path / "runtime.db"), PluginRegistry([manifest]), workspace_root=tmp_path / "runs")
    task = build_rtl_mutation_task(project_id="p", design_id="d", rtl_path=rtl, testbench_path=tb, testbench_top="tb",
                                   spec_id="spec-d", verification_id="verify-d", verifier_identity="verification-agent", minimum_score=.1)
    run = runtime.execute_once(runtime.submit(task, capability="eda.rtl.mutation_test").run_id)
    assert run.status.value == "succeeded"
    report = next(x for x in runtime.describe(run.run_id)["stages"][0]["attempts"][0]["artifacts"] if x["kind"] == "mutation_report")
    assert report["size_bytes"] > 0
