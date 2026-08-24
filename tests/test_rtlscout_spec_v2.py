import sys
from pathlib import Path

from openroad_platform_contracts import PortSpec, SpecIR, VerificationPackage
from openroad_platform_execution import PluginRegistry, build_rtlscout_spec_task, rtlscout_plugin_manifest
from openroad_platform_scheduler import RuntimeStore, WorkflowRuntime

from test_rtlscout_plugin import fake_source, _executable


def test_rtlscout_v2_accepts_specir_not_benchmark_and_preserves_oracle(tmp_path):
    source, commit = fake_source(tmp_path)
    plugin = rtlscout_plugin_manifest(source, sys.executable,
        verilator_bin=_executable(tmp_path / "bin/verilator"), yosys_bin=_executable(tmp_path / "bin/yosys"),
        expected_commit=commit)
    spec = SpecIR("specir-gcd", "gcd", "generated_top", "identity datapath", "functional pass",
                  (PortSpec("a", "input", 2), PortSpec("y", "output", 2)),
                  acceptance_criteria=("testbench passes",))
    package = VerificationPackage("verify-gcd", spec.spec_id, ("verilator-lint",),
                                  simulation_oracle_refs=("artifact:verification-oracle:" + "a" * 64,))
    task = build_rtlscout_spec_task(project_id="p", spec=spec, verification=package,
                                    testbench_source="module tb; generated_top dut(); initial begin if (1'b0) $fatal(1, \"check\"); $display(\"PASS\"); $finish; end endmodule\n",
                                    model="fake:simple_adder_pass", max_steps=3)
    assert "benchmark" not in task.inputs and task.inputs["mode"] == "specir-v2"
    runtime = WorkflowRuntime(RuntimeStore(tmp_path / "runtime.db"), PluginRegistry([plugin]),
                              workspace_root=tmp_path / "runs")
    finished = runtime.execute_once(runtime.submit(task).run_id)
    assert finished.status.value == "succeeded"
    view = runtime.describe(finished.run_id)
    rtl = next(item for item in view["stages"][0]["attempts"][0]["artifacts"] if item["kind"] == "rtl")
    workspace = Path(view["stages"][0]["attempts"][0]["workspace"])
    assert "module generated_top" in (workspace / rtl["store_key"]).read_text()
    assert '"top": "generated_top"' in (workspace / "specir-benchmarks/specir-v2/description.txt").read_text()
    assert (workspace / "specir-benchmarks/specir-v2/specir.json").is_file()


def test_api_specir_submission_registers_only_runtime_produced_candidate(tmp_path):
    """The public v2 entry reaches candidate lineage without a benchmark API."""
    from apps.api.app import ApiState

    source, commit = fake_source(tmp_path)
    plugin = rtlscout_plugin_manifest(source, sys.executable,
        verilator_bin=_executable(tmp_path / "bin/verilator"), yosys_bin=_executable(tmp_path / "bin/yosys"),
        expected_commit=commit)
    state = ApiState(tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
                     design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
                     yosys_bin=tmp_path / "bin/yosys", runtime_db_path=tmp_path / "runtime-api.db",
                     campaign_db_path=tmp_path / "campaign.db")
    # Replace the production manifest with the isolated upstream fixture.
    state.runtime.registry._manifests[(plugin.plugin_id, plugin.plugin_version)] = plugin
    state.rtlscout_readiness = {"ready": True, "reason": "fixture"}
    spec = SpecIR("specir-api", "api_design", "generated_top", "identity datapath", "functional pass",
                  (PortSpec("a", "input", 2), PortSpec("y", "output", 2)),
                  acceptance_criteria=("testbench passes",))
    state.rtl_frontend.add_spec(spec)
    submitted = state.submit_rtlscout_spec(spec.spec_id, {
        "testbench_source": "module tb; generated_top dut(); initial begin if (1'b0) $fatal(1, \"check\"); $display(\"PASS\"); $finish; end endmodule\n",
        "oracle_origin": "user_authored", "oracle_reviewed_by": "test-reviewer",
        "model": "fake:simple_adder_pass", "max_steps": 3,
    })
    receipt = state.verification_oracle_root / f"{submitted['testbench_sha256']}.{spec.spec_id}.approval.json"
    assert receipt.is_file()
    assert '"origin": "user_authored"' in receipt.read_text()
    run_id = submitted["run"]["run"]["run_id"]
    assert state.runtime.execute_once(run_id).status.value == "succeeded"
    recorded = state.auto_collect_terminal_run(run_id)
    lineage = state.rtl_frontend.lineage(spec.spec_id)
    assert recorded["action"] == "rtlscout_candidate"
    assert lineage["candidates"][0]["generator"] == "rtlscout-v2"
    assert lineage["candidates"][0]["rtl_artifact_ref"].startswith("artifact:rtl-candidate:")


def test_api_rejects_a_non_self_checking_oracle_before_rtlscout_submission(tmp_path):
    from apps.api.app import ApiState
    state = ApiState(tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
                     design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
                     yosys_bin=tmp_path / "missing-yosys", runtime_db_path=tmp_path / "runtime.db")
    spec = SpecIR("specir-oracle", "oracle_design", "generated_top", "identity", "functional",
                  (PortSpec("a", "input", 1), PortSpec("y", "output", 1)),
                  acceptance_criteria=("testbench passes",))
    state.rtl_frontend.add_spec(spec)
    state.rtlscout_readiness = {"ready": True, "reason": "fixture"}
    import pytest
    with pytest.raises(ValueError, match="self-checking"):
        state.submit_rtlscout_spec(spec.spec_id, {
            "testbench_source": "module tb; generated_top dut(); initial $display(\"PASS\"); endmodule",
            "oracle_origin": "user_authored", "oracle_reviewed_by": "reviewer", "model": "fixture",
        })


def test_production_specir_entry_rejects_user_selected_model_or_secret_profile(tmp_path):
    """Internal v2 has one server-owned Codex authority, not a hidden BYOK path."""
    from apps.api.app import ApiState
    import pytest
    state = ApiState(tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
                     design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
                     yosys_bin=tmp_path / "missing-yosys", runtime_db_path=tmp_path / "runtime.db")
    spec = SpecIR("specir-provider", "provider_design", "generated_top", "identity", "functional",
                  (PortSpec("a", "input", 1), PortSpec("y", "output", 1)),
                  acceptance_criteria=("testbench passes",))
    state.rtl_frontend.add_spec(spec)
    state.rtlscout_readiness = {"ready": True, "reason": "production"}
    payload = {"testbench_source": "module tb; generated_top dut(); initial begin if (1'b0) $fatal; $display(\"PASS\"); $finish; end endmodule",
               "oracle_origin": "user_authored", "oracle_reviewed_by": "reviewer"}
    with pytest.raises(ValueError, match="only the platform-managed codex-cli"):
        state.submit_rtlscout_spec(spec.spec_id, {**payload, "model": "fake:fixture"})
    with pytest.raises(ValueError, match="profiles are disabled"):
        state.submit_rtlscout_spec(spec.spec_id, {**payload, "profile_id": "external", "secret_handle": "x"})


def test_codex_testbench_draft_is_response_only_and_requires_oracle_review(monkeypatch):
    import json
    import subprocess
    from apps.api.app import _codex_testbench_draft

    def fake_run(command, **kwargs):
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(json.dumps({
            "testbench_source": "module tb; logic [1:0] a; wire [1:0] y; generated_top dut(.a(a), .y(y)); initial begin a=0; #1; if (y !== a) $fatal(1, \"bad\"); $display(\"PASS\"); $finish; end endmodule\n",
            "assumptions": ["combinational identity"], "coverage_plan": ["input values"], "open_questions": [],
        }))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("apps.api.app.shutil.which", lambda name: "/fake/codex")
    monkeypatch.setattr("apps.api.app.subprocess.run", fake_run)
    spec = SpecIR("specir-draft", "draft", "generated_top", "identity", "functional",
                  (PortSpec("a", "input", 2), PortSpec("y", "output", 2)),
                  acceptance_criteria=("output matches input",))
    result = _codex_testbench_draft(spec)
    assert result["structural_floor_passed"] is True
    assert result["execution_allowed"] is False
    assert "not an oracle" in result["authority"]


def test_codex_rtlscout_bridge_judges_an_absolute_candidate_path(tmp_path, monkeypatch):
    """Regression: run_eval resolves its positional RTL file before --workdir."""
    import hashlib
    import subprocess
    from openroad_platform_execution.rtlscout_adapter import _codex_cli_candidates
    bench = tmp_path / "bench"; bench.mkdir()
    tb = "module tb; endmodule\n"; (bench / "tb.sv").write_text(tb)
    workspace = tmp_path / "workspace"; workspace.mkdir()

    def fake_run(command, **kwargs):
        if "codex" in str(command[0]):
            (Path(kwargs["cwd"]) / "design.sv").write_text("module dut; endmodule\n")
            return subprocess.CompletedProcess(command, 0, "candidate", "")
        assert Path(command[2]).is_absolute()
        saved = Path(command[command.index("--save-to") + 1]); saved.mkdir()
        (saved / "result.json").write_text(__import__("json").dumps({
            "passed": True, "cost_value": 7, "metrics": {"num_cells": 7},
            "correctness": {"lint_ok": True, "sim_ok": True},
        }))
        return subprocess.CompletedProcess(command, 0, "judge", "")

    monkeypatch.setattr("openroad_platform_execution.rtlscout_adapter.shutil.which", lambda _: "/fake/codex")
    monkeypatch.setattr("openroad_platform_execution.rtlscout_adapter.subprocess.run", fake_run)
    result = _codex_cli_candidates(
        source=tmp_path / "source", python=Path(sys.executable), workspace=workspace, bench=bench,
        spec={"top": "dut"}, testbench_sha256=hashlib.sha256(tb.encode()).hexdigest(),
        model="gpt-5.6-terra", max_steps=1, cost_metric="yosys_cells", log_path=workspace / "bridge.log",
    )
    assert result["passed"] is True
    assert result["best_cost"] == 7.0
