import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from openroad_platform_contracts import PortSpec, SpecIR, VerificationPackage
from openroad_platform_execution import PluginRegistry, build_rtlscout_spec_task, rtlscout_plugin_manifest
from openroad_platform_scheduler import RuntimeStore, WorkflowRuntime

from test_rtlscout_plugin import fake_source, _executable


SELF_CHECKING_TB = """module tb;
generated_top dut();
initial begin
  if (1'b0) $fatal(1, "check");
  $display("TB_SUMMARY total=1 errors=0");
  $display("PASS");
  $finish;
end
endmodule
"""


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
                                    testbench_source=SELF_CHECKING_TB,
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
                     yosys_bin=tmp_path / "bin/yosys", runtime_db_path=tmp_path / "runtime-api.db")
    # Replace the production manifest with the isolated upstream fixture.
    state.runtime.registry._manifests[(plugin.plugin_id, plugin.plugin_version)] = plugin
    state.rtlscout_readiness = {"ready": True, "reason": "fixture"}
    spec = SpecIR("specir-api", "api_design", "generated_top", "identity datapath", "functional pass",
                  (PortSpec("a", "input", 2), PortSpec("y", "output", 2)),
                  acceptance_criteria=("testbench passes",))
    state.rtl_frontend.add_spec(spec)
    submitted = state.submit_rtlscout_spec(spec.spec_id, {
        "testbench_source": SELF_CHECKING_TB, "testbench_top": "tb",
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
    assert lineage["candidates"][0]["provenance"]["oracle_origin"] == "user_authored"


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
    payload = {"testbench_source": SELF_CHECKING_TB, "testbench_top": "tb",
               "oracle_origin": "user_authored", "oracle_reviewed_by": "reviewer"}
    with pytest.raises(ValueError, match="fixed platform Codex model"):
        state.submit_rtlscout_spec(spec.spec_id, {**payload, "model": "fake:fixture"})
    with pytest.raises(ValueError, match="profiles are disabled"):
        state.submit_rtlscout_spec(spec.spec_id, {**payload, "profile_id": "external", "secret_handle": "x"})


def test_codex_testbench_draft_is_structured_independent_verifier_input(monkeypatch):
    import json
    import subprocess
    from apps.api.app import _codex_testbench_draft

    def fake_run(command, **kwargs):
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(json.dumps({
            "testbench_source": "module tb; logic [1:0] a; wire [1:0] y; generated_top dut(.a(a), .y(y)); initial begin a=0; #1; if (y !== a) $fatal(1, \"bad\"); $display(\"TB_SUMMARY total=1 errors=0\"); $display(\"PASS\"); $finish; end endmodule\n",
            "testbench_top": "tb",
            "assumptions": ["combinational identity"], "coverage_plan": ["input values"], "open_questions": [],
        }))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("apps.api.app.shutil.which", lambda name: "/fake/codex")
    monkeypatch.setattr("apps.api.app.subprocess.run", fake_run)
    # This unit test isolates the structured Codex exchange.  The real dual-
    # compiler preflight has its own regression test below and must not be
    # routed through this Codex-only subprocess stub.
    monkeypatch.setattr("apps.api.app._compile_testbench_preflight", lambda spec, source: None)
    spec = SpecIR("specir-draft", "draft", "generated_top", "identity", "functional",
                  (PortSpec("a", "input", 2), PortSpec("y", "output", 2)),
                  acceptance_criteria=("output matches input",))
    result = _codex_testbench_draft(spec)
    assert result["structural_floor_passed"] is True
    assert result["execution_allowed"] is False
    assert "verification-agent output preview" in result["authority"]


def test_automatic_dual_agent_entry_creates_independent_oracle(tmp_path, monkeypatch):
    """The normal v2 route needs no human TB review field or benchmark input."""
    from apps.api.app import ApiState
    source, commit = fake_source(tmp_path)
    plugin = rtlscout_plugin_manifest(source, sys.executable,
        verilator_bin=_executable(tmp_path / "bin/verilator"), yosys_bin=_executable(tmp_path / "bin/yosys"),
        expected_commit=commit)
    state = ApiState(tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
                     design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
                     yosys_bin=tmp_path / "bin/yosys", runtime_db_path=tmp_path / "runtime.db")
    state.runtime.registry._manifests[(plugin.plugin_id, plugin.plugin_version)] = plugin
    state.rtlscout_readiness = {"ready": True, "reason": "fixture"}
    spec = SpecIR("specir-auto", "auto_design", "generated_top", "identity", "functional",
                  (PortSpec("a", "input", 2), PortSpec("y", "output", 2)),
                  acceptance_criteria=("output matches input",))
    state.rtl_frontend.add_spec(spec)
    monkeypatch.setattr("apps.api.app._codex_testbench_draft", lambda _spec: {
        "draft": {"testbench_source": "module tb; logic [1:0] a; wire [1:0] y; generated_top dut(.a(a), .y(y)); initial begin a=0; #1; if (y !== a) $fatal; $display(\"TB_SUMMARY total=1 errors=0\"); $display(\"PASS\"); $finish; end endmodule\n", "testbench_top": "tb"},
        "draft_sha256": "b" * 64, "structural_floor_passed": True,
    })
    submitted = state.submit_automated_rtlscout(spec.spec_id, {"model": "fake:simple_adder_pass"})
    assert submitted["automation"]["human_required"] is False
    assert submitted["automation"]["verification_agent"] == "verification-agent-v2"
    receipt = state.verification_oracle_root / f"{submitted['testbench_sha256']}.{spec.spec_id}.approval.json"
    assert '"origin": "independent_verifier_agent"' in receipt.read_text()


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


def test_automated_pipeline_runs_all_runtime_gates_to_baseline(tmp_path, monkeypatch):
    from apps.api.app import ApiState
    state = ApiState(tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
                     design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
                     yosys_bin=tmp_path / "missing-yosys", runtime_db_path=tmp_path / "runtime.db")
    receipt = lambda run_id: {"run": {"run": {"run_id": run_id, "status": "queued"}}}
    monkeypatch.setattr(state, "submit_automated_rtlscout", lambda *a, **k: receipt("run-rtl"))
    monkeypatch.setattr(state, "submit_rtl_verification", lambda *a, **k: receipt("run-verify"))
    monkeypatch.setattr(state, "submit_rtl_simulation", lambda *a, **k: receipt("run-sim"))
    monkeypatch.setattr(state, "submit_rtl_mutation_test", lambda *a, **k: receipt("run-mutation"))
    monkeypatch.setattr(state, "promote_verified_rtl_to_orfs", lambda *a, **k: receipt("run-orfs"))
    monkeypatch.setattr(state.runtime, "execute_once",
                        lambda run_id: SimpleNamespace(status=SimpleNamespace(value="succeeded")))
    monkeypatch.setattr(state, "auto_collect_terminal_run",
                        lambda run_id: {"run_id": run_id, "action": "recorded",
                                        "status": "passed",
                                        **({"candidate_id": "candidate-pinned"}
                                           if run_id == "run-rtl" else {})})
    result = state.run_automated_rtl_pipeline("spec-auto", {})
    assert result["status"] == "baseline_succeeded"
    assert [step["role"] for step in result["steps"]] == [
        "rtlscout", "compile_lint", "simulation", "mutation_quality", "orfs_baseline"]


def test_generated_testbench_is_compiled_before_it_can_be_frozen() -> None:
    from apps.api.app import _compile_testbench_preflight

    spec = SpecIR(
        "specir-tb-preflight", "uart-preflight", "uart_tx",
        "serialize bytes", "functional",
        (PortSpec("clk", "input"), PortSpec("tx", "output")),
    )
    valid = """
module tb;
  logic clk;
  wire tx;
  uart_tx dut(.clk(clk), .tx(tx));
  initial begin
    clk = 1'b0;
    $display("TB_SUMMARY total=1 errors=0");
    $display("PASS");
    $finish;
  end
endmodule
"""
    _compile_testbench_preflight(spec, valid)

    invalid = valid.replace("clk = 1'b0;", "clk = 8'hA5[0];")
    with pytest.raises(ValueError, match="rejected the generated testbench"):
        _compile_testbench_preflight(spec, invalid)


def test_orfs_promotion_never_inherits_a_frontend_synthesis_only_target(tmp_path, monkeypatch):
    """A verified RTL promotion is always a complete backend baseline."""
    from apps.api.app import ApiState
    state = ApiState(tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
                     design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
                     yosys_bin=tmp_path / "missing-yosys", runtime_db_path=tmp_path / "runtime.db")
    source = tmp_path / "verified.sv"; source.write_text("module generated_top; endmodule\n")
    lineage = {
        "spec": {"design_id": "temporary-spec-design", "top": "generated_top",
                 "clock": None, "constraints": {"target_stage": "synth",
                                                   "platform": "nangate45"}},
        "candidates": [{"candidate_id": "candidate-1", "provenance": {}}],
        "checks": [
            {"candidate_id": "candidate-1", "check_kind": "compile_lint",
             "status": "passed", "detail": {"run_id": "verify-run"}},
            {"candidate_id": "candidate-1", "check_kind": "simulation",
             "status": "passed", "detail": {"run_id": "sim-run"}},
        ],
    }
    monkeypatch.setattr(state, "get_rtl_lineage", lambda *a, **k: lineage)
    monkeypatch.setattr(state, "_pinned_rtl_candidate", lambda *_: lineage["candidates"][0])
    monkeypatch.setattr(state.runtime, "describe", lambda _rid: {"stages": [{"attempts": [{
        "status": "succeeded", "workspace": str(tmp_path), "artifacts": [{
            "kind": "rtl", "store_key": source.name, "sha256": __import__("hashlib").sha256(source.read_bytes()).hexdigest()
        }]}]}]})
    submitted = {}
    monkeypatch.setattr(state.runtime, "submit", lambda task, **_k: submitted.setdefault("task", task) or None)
    monkeypatch.setattr(state, "get_runtime_run", lambda *_a, **_k: {"run": {"run_id": "orfs"}})
    class Receipt: run_id = "orfs"
    monkeypatch.setattr(state.runtime, "submit", lambda task, **_k: submitted.update(task=task) or Receipt())
    state.promote_verified_rtl_to_orfs("spec-1", candidate_id="candidate-1")
    assert submitted["task"].parameters["target_stage"] == "finish"


def test_automated_pipeline_routes_weak_mutation_to_verification_revision(tmp_path, monkeypatch):
    from apps.api.app import ApiState
    state = ApiState(tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
                     design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
                     yosys_bin=tmp_path / "missing-yosys", runtime_db_path=tmp_path / "runtime.db")
    receipt = lambda run_id: {"run": {"run": {"run_id": run_id, "status": "queued"}}}
    monkeypatch.setattr(state, "submit_automated_rtlscout", lambda *a, **k: receipt("run-rtl"))
    monkeypatch.setattr(state, "submit_rtl_verification", lambda *a, **k: receipt("run-verify"))
    monkeypatch.setattr(state, "submit_rtl_simulation", lambda *a, **k: receipt("run-sim"))
    monkeypatch.setattr(state, "submit_rtl_mutation_test", lambda *a, **k: receipt("run-mutation"))
    monkeypatch.setattr(state.runtime, "execute_once", lambda run_id: SimpleNamespace(
        status=SimpleNamespace(value="failed" if run_id == "run-mutation" else "succeeded")))
    monkeypatch.setattr(state, "auto_collect_terminal_run",
                        lambda run_id: {"run_id": run_id, "action": "recorded",
                                        **({"candidate_id": "candidate-pinned"}
                                           if run_id == "run-rtl" else {})})
    result = state.run_automated_rtl_pipeline("spec-auto", {})
    assert result["status"] == "stopped"
    assert result["boundary"] == "verification_revision_required"
    assert result["steps"][-1]["role"] == "mutation_quality"
    assert result["rtl_revision"] == 2
    assert len(result["revision_history"]) == 3


def test_automated_pipeline_revision_separates_verifier_feedback_from_rtl_author(
        tmp_path, monkeypatch):
    """A weak oracle is revised by the verifier, then RTLScout sees a new frozen package."""
    from apps.api.app import ApiState
    state = ApiState(tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
                     design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
                     yosys_bin=tmp_path / "missing-yosys", runtime_db_path=tmp_path / "runtime.db")
    receipt = lambda run_id: {"run": {"run": {"run_id": run_id, "status": "queued"}}}
    rtl_payloads = []
    mutation_calls = 0

    def rtl_submit(_spec, submitted_payload, **_kwargs):
        revision = len(rtl_payloads)
        rtl_payloads.append(dict(submitted_payload))
        return {**receipt(f"run-rtl-{revision}"), "verification_id": f"verify-{revision}"}

    def mutation_submit(*_args, **_kwargs):
        nonlocal mutation_calls
        run_id = f"run-mutation-{mutation_calls}"
        mutation_calls += 1
        return receipt(run_id)

    monkeypatch.setattr(state, "submit_automated_rtlscout", rtl_submit)
    monkeypatch.setattr(state, "submit_rtl_verification",
                        lambda *a, **k: receipt(f"run-verify-{k['candidate_id']}"))
    monkeypatch.setattr(state, "submit_rtl_simulation",
                        lambda *a, **k: receipt(f"run-sim-{k['candidate_id']}"))
    monkeypatch.setattr(state, "submit_rtl_mutation_test", mutation_submit)
    monkeypatch.setattr(state, "promote_verified_rtl_to_orfs",
                        lambda *a, **k: receipt("run-orfs"))
    monkeypatch.setattr(state.runtime, "execute_once",
                        lambda run_id: SimpleNamespace(status=SimpleNamespace(value="succeeded")))

    def collect(run_id):
        if run_id.startswith("run-rtl-"):
            return {"status": "passed", "candidate_id": f"candidate-{run_id[-1]}"}
        if run_id == "run-mutation-0":
            return {"status": "failed", "mutation_score": .2, "survived": 8}
        return {"status": "passed"}

    monkeypatch.setattr(state, "auto_collect_terminal_run", collect)
    result = state.run_automated_rtl_pipeline(
        "spec-revision", {"max_revisions": 2})

    assert result["status"] == "baseline_succeeded"
    assert result["rtl_revision"] == 1
    assert len(rtl_payloads) == 2
    assert "verification_feedback" not in rtl_payloads[0]
    assert "mutation_score" in rtl_payloads[1]["verification_feedback"]
    assert [step["role"] for step in result["steps"]].count("rtlscout") == 2
    assert [step["role"] for step in result["steps"]].count("mutation_quality") == 2


def test_automated_pipeline_stops_when_mutation_runtime_succeeds_but_gate_fails(
        tmp_path, monkeypatch):
    """The mutation adapter exits successfully even when its quality gate rejects TB."""
    from apps.api.app import ApiState
    state = ApiState(tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
                     design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
                     yosys_bin=tmp_path / "missing-yosys", runtime_db_path=tmp_path / "runtime.db")
    receipt = lambda run_id: {"run": {"run": {"run_id": run_id, "status": "queued"}}}
    monkeypatch.setattr(state, "submit_automated_rtlscout", lambda *a, **k: receipt("run-rtl"))
    monkeypatch.setattr(state, "submit_rtl_verification", lambda *a, **k: receipt("run-verify"))
    monkeypatch.setattr(state, "submit_rtl_simulation", lambda *a, **k: receipt("run-sim"))
    monkeypatch.setattr(state, "submit_rtl_mutation_test", lambda *a, **k: receipt("run-mutation"))
    promoted = []
    monkeypatch.setattr(state, "promote_verified_rtl_to_orfs",
                        lambda *a, **k: promoted.append(True) or receipt("run-orfs"))
    monkeypatch.setattr(state.runtime, "execute_once",
                        lambda run_id: SimpleNamespace(status=SimpleNamespace(value="succeeded")))
    monkeypatch.setattr(state, "auto_collect_terminal_run", lambda run_id: {
        "run_id": run_id, "action": "rtl_mutation_quality" if run_id == "run-mutation" else "recorded",
        "status": "failed" if run_id == "run-mutation" else "passed",
        **({"candidate_id": "candidate-pinned"} if run_id == "run-rtl" else {}),
    })

    result = state.run_automated_rtl_pipeline("spec-auto", {})

    assert result["status"] == "stopped"
    assert result["boundary"] == "verification_revision_required"
    assert result["steps"][-1]["status"] == "succeeded"
    assert result["steps"][-1]["gate_failure"]["observed"] == "failed"
    assert promoted == []


def test_automated_pipeline_returns_boundary_when_promotion_gate_rejects(tmp_path, monkeypatch):
    from apps.api.app import ApiState
    state = ApiState(tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
                     design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
                     yosys_bin=tmp_path / "missing-yosys", runtime_db_path=tmp_path / "runtime.db")
    receipt = lambda run_id: {"run": {"run": {"run_id": run_id, "status": "queued"}}}
    monkeypatch.setattr(state, "submit_automated_rtlscout", lambda *a, **k: receipt("run-rtl"))
    monkeypatch.setattr(state, "submit_rtl_verification", lambda *a, **k: receipt("run-verify"))
    monkeypatch.setattr(state, "submit_rtl_simulation", lambda *a, **k: receipt("run-sim"))
    monkeypatch.setattr(state, "submit_rtl_mutation_test", lambda *a, **k: receipt("run-mutation"))
    monkeypatch.setattr(state.runtime, "execute_once",
                        lambda run_id: SimpleNamespace(status=SimpleNamespace(value="succeeded")))
    monkeypatch.setattr(state, "auto_collect_terminal_run",
                        lambda run_id: {"run_id": run_id, "status": "passed",
                                        **({"candidate_id": "candidate-pinned"}
                                           if run_id == "run-rtl" else {})})
    monkeypatch.setattr(state, "promote_verified_rtl_to_orfs",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("mutation gate rejected")))

    result = state.run_automated_rtl_pipeline("spec-auto", {})

    assert result["status"] == "stopped"
    assert result["boundary"] == "verification_revision_required"
    assert result["steps"][-1]["role"] == "promotion_gate"


def test_automated_pipeline_is_idempotent_and_pins_candidate(tmp_path, monkeypatch):
    from apps.api.app import ApiState
    state = ApiState(tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
                     design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
                     yosys_bin=tmp_path / "missing-yosys", runtime_db_path=tmp_path / "runtime.db")
    receipt = lambda run_id: {"run": {"run": {"run_id": run_id, "status": "queued"}}}
    submissions = []
    monkeypatch.setattr(state, "submit_automated_rtlscout",
                        lambda *a, **k: submissions.append(("rtl", k)) or receipt("run-rtl"))
    monkeypatch.setattr(state, "submit_rtl_verification",
                        lambda *a, **k: submissions.append(("verify", k)) or receipt("run-verify"))
    monkeypatch.setattr(state, "submit_rtl_simulation",
                        lambda *a, **k: submissions.append(("sim", k)) or receipt("run-sim"))
    monkeypatch.setattr(state, "submit_rtl_mutation_test",
                        lambda *a, **k: submissions.append(("mutation", k)) or receipt("run-mutation"))
    monkeypatch.setattr(state, "promote_verified_rtl_to_orfs",
                        lambda *a, **k: submissions.append(("orfs", k)) or receipt("run-orfs"))
    monkeypatch.setattr(state.runtime, "execute_once",
                        lambda run_id: SimpleNamespace(status=SimpleNamespace(value="succeeded")))
    monkeypatch.setattr(state, "auto_collect_terminal_run", lambda run_id: {
        "run_id": run_id, "status": "passed",
        **({"candidate_id": "candidate-fixed"} if run_id == "run-rtl" else {}),
    })

    first = state.run_automated_rtl_pipeline("spec-idempotent", {})
    second = state.run_automated_rtl_pipeline("spec-idempotent", {})

    assert first["pipeline_id"] == second["pipeline_id"]
    assert second["resumed"] is True
    assert len(submissions) == 5
    assert all(item[1].get("candidate_id") == "candidate-fixed"
               for item in submissions if item[0] != "rtl")
