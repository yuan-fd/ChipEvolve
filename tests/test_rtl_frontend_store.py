from __future__ import annotations

import hashlib

import pytest

from apps.api.app import ApiState
from openroad_platform_contracts import PortSpec, RTLCandidate, SpecIR, VerificationPackage
from openroad_platform_contracts import RuntimeStatus, TaskSpec
from openroad_platform_scheduler import RTLFrontendStore


def _spec() -> SpecIR:
    return SpecIR(
        spec_id="spec-uart-v1", design_id="uart", top="uart",
        functionality="Transmit an 8-N-1 serial frame.", objective="functional first",
        ports=(PortSpec("clk", "input"), PortSpec("tx", "output")), clock="clk",
        acceptance_criteria=("A start bit precedes eight data bits.",),
    )


def test_rtl_frontend_store_keeps_spec_oracle_candidate_and_checks_separate(tmp_path):
    store = RTLFrontendStore(tmp_path / "rtl.db")
    spec = _spec()
    package = VerificationPackage(
        verification_id="verify-uart-v1", spec_id=spec.spec_id,
        compile_checks=("verilator-lint",), simulation_oracle_refs=("artifact:uart-tb",),
    )
    candidate = RTLCandidate(
        candidate_id="candidate-uart-1", spec_id=spec.spec_id,
        verification_id=package.verification_id, rtl_artifact_ref="artifact:rtl-uart-1",
        generator="rtlscout-v2",
    )
    store.add_spec(spec)
    store.add_verification_package(package)
    store.add_candidate(candidate)
    store.add_check(
        check_id="check-uart-lint", candidate_id=candidate.candidate_id,
        check_kind="verilator-lint", status="passed", evidence_ref="artifact:lint-log",
        evidence_sha256=hashlib.sha256(b"lint").hexdigest(), detail={"warnings": 0},
    )
    lineage = store.lineage(spec.spec_id)
    assert lineage["spec"]["spec_id"] == spec.spec_id
    assert lineage["candidates"][0]["rtl_artifact_ref"] == "artifact:rtl-uart-1"
    assert lineage["checks"][0]["status"] == "passed"
    with pytest.raises(ValueError, match="immutable"):
        store.add_spec(spec)


def test_candidate_cannot_use_an_oracle_from_another_spec(tmp_path):
    store = RTLFrontendStore(tmp_path / "rtl.db")
    spec = _spec()
    other = SpecIR(
        spec_id="spec-other-v1", design_id="other", top="other", functionality="other",
        objective="other", ports=(PortSpec("a", "input"),), acceptance_criteria=("build",),
    )
    package = VerificationPackage("verify-other-v1", other.spec_id, ("lint",))
    store.add_spec(spec)
    store.add_spec(other)
    store.add_verification_package(package)
    with pytest.raises(ValueError, match="another SpecIR"):
        store.add_candidate(RTLCandidate(
            candidate_id="bad-candidate", spec_id=spec.spec_id,
            verification_id=package.verification_id, rtl_artifact_ref="artifact:bad",
            generator="rtlscout-v2",
        ))


def test_terminal_rtl_verify_run_is_written_back_as_a_candidate_gate(tmp_path):
    state = ApiState(tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
                     design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
                     yosys_bin=tmp_path / "missing-yosys", runtime_db_path=tmp_path / "runtime.db")
    spec = _spec(); package = VerificationPackage("verify-uart-v1", spec.spec_id, ("lint",))
    candidate = RTLCandidate("candidate-uart-1", spec.spec_id, package.verification_id,
                             "artifact:rtl-uart-1", "rtlscout-v2")
    state.rtl_frontend.add_spec(spec); state.rtl_frontend.add_verification_package(package)
    state.rtl_frontend.add_candidate(candidate)
    task = TaskSpec(task_id="verify-runtime-1", project_id="p", design_id="uart", plugin_id="rtl-verify",
                    inputs={"rtl": {"sha256": "a" * 64}, "top": "uart"},
                    labels={"candidate_id": candidate.candidate_id})
    run, stage = state.runtime_store.submit_plugin_run(task, plugin_version="1.0.0")
    workspace = tmp_path / "attempt"; workspace.mkdir()
    attempt = state.runtime_store.start_attempt(stage.stage_run_id, worker_id="test", workspace=workspace, lease_seconds=30)
    report = workspace / "report.json"; report.write_text("{}")
    state.runtime_store.register_artifact(attempt.attempt_id, kind="verification_report", store_key="report.json",
                                          size_bytes=2, sha256=hashlib.sha256(b"{}").hexdigest())
    state.runtime_store.finish_attempt(attempt.attempt_id, RuntimeStatus.SUCCEEDED, exit_code=0)
    result = state.record_rtl_verification_run(run.run_id)
    assert result["status"] == "passed"
    assert state.rtl_frontend.lineage(spec.spec_id)["checks"][0]["check_kind"] == "compile_lint"


def test_compile_only_rtl_can_never_be_promoted_to_orfs(tmp_path, monkeypatch):
    state = ApiState(tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
                     design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
                     yosys_bin=tmp_path / "missing-yosys", runtime_db_path=tmp_path / "runtime.db")
    spec = _spec(); package = VerificationPackage("verify-uart-v1", spec.spec_id, ("lint",))
    candidate = RTLCandidate("candidate-uart-1", spec.spec_id, package.verification_id,
                             "artifact:rtl-uart-1", "rtlscout-v2")
    state.rtl_frontend.add_spec(spec); state.rtl_frontend.add_verification_package(package)
    state.rtl_frontend.add_candidate(candidate)
    # This gate is evaluated before any design file or ORFS interaction.
    monkeypatch.setattr(state, "get_rtl_lineage", lambda *args, **kwargs: state.rtl_frontend.lineage(spec.spec_id))
    state.rtl_frontend.add_check(
        check_id="lint-only", candidate_id=candidate.candidate_id, check_kind="compile_lint",
        status="passed", evidence_ref="artifact:lint", evidence_sha256=hashlib.sha256(b"lint").hexdigest(),
    )
    with pytest.raises(ValueError, match="simulation, formal, or equivalence"):
        state.promote_verified_rtl_to_orfs(spec.spec_id)


def test_automatic_verifier_oracle_cannot_bypass_mutation_gate(tmp_path, monkeypatch):
    state = ApiState(tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
                     design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
                     yosys_bin=tmp_path / "missing-yosys", runtime_db_path=tmp_path / "runtime.db")
    spec = _spec(); package = VerificationPackage("verify-auto-v1", spec.spec_id, ("lint",))
    candidate = RTLCandidate(
        "candidate-auto-1", spec.spec_id, package.verification_id,
        "artifact:rtl-auto-1", "rtlscout-v2",
        provenance={"oracle_provenance": {
            "origin": "independent_verifier_agent",
            "reviewed_by": "verification-agent-v2/codex-cli",
        }},
    )
    state.rtl_frontend.add_spec(spec); state.rtl_frontend.add_verification_package(package)
    state.rtl_frontend.add_candidate(candidate)
    monkeypatch.setattr(state, "get_rtl_lineage",
                        lambda *args, **kwargs: state.rtl_frontend.lineage(spec.spec_id))
    for check_id, kind in (("compile-auto", "compile_lint"), ("sim-auto", "simulation")):
        state.rtl_frontend.add_check(
            check_id=check_id, candidate_id=candidate.candidate_id, check_kind=kind,
            status="passed", evidence_ref=f"artifact:{check_id}",
            evidence_sha256=hashlib.sha256(check_id.encode()).hexdigest(),
            detail={"run_id": f"run-{check_id}"},
        )
    with pytest.raises(ValueError, match="generated verification oracle.*mutation-quality"):
        state.promote_verified_rtl_to_orfs(spec.spec_id)


def test_terminal_rtl_sim_run_is_written_back_as_functional_gate(tmp_path):
    state = ApiState(tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
                     design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
                     yosys_bin=tmp_path / "missing-yosys", runtime_db_path=tmp_path / "runtime.db")
    spec = _spec(); package = VerificationPackage("verify-uart-v1", spec.spec_id, ("lint",))
    candidate = RTLCandidate("candidate-uart-1", spec.spec_id, package.verification_id,
                             "artifact:rtl-uart-1", "rtlscout-v2")
    state.rtl_frontend.add_spec(spec); state.rtl_frontend.add_verification_package(package); state.rtl_frontend.add_candidate(candidate)
    task = TaskSpec(task_id="simulate-runtime-1", project_id="p", design_id="uart", plugin_id="rtl-sim",
                    inputs={"rtl": {"sha256": "a" * 64}, "testbench": {"sha256": "b" * 64}, "top": "tb"},
                    labels={"candidate_id": candidate.candidate_id})
    run, stage = state.runtime_store.submit_plugin_run(task, plugin_version="1.0.0")
    workspace = tmp_path / "attempt"; workspace.mkdir()
    attempt = state.runtime_store.start_attempt(stage.stage_run_id, worker_id="test", workspace=workspace, lease_seconds=30)
    report = workspace / "report.json"; report.write_text("{}")
    state.runtime_store.register_artifact(attempt.attempt_id, kind="simulation_report", store_key="report.json", size_bytes=2, sha256=hashlib.sha256(b"{}").hexdigest())
    state.runtime_store.finish_attempt(attempt.attempt_id, RuntimeStatus.SUCCEEDED, exit_code=0)
    assert state.record_rtl_simulation_run(run.run_id)["status"] == "passed"
    assert state.rtl_frontend.lineage(spec.spec_id)["checks"][0]["check_kind"] == "simulation"


def test_attach_simulation_oracle_derives_immutable_child_candidate(tmp_path, monkeypatch):
    state = ApiState(tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
                     design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
                     yosys_bin=tmp_path / "missing-yosys", runtime_db_path=tmp_path / "runtime.db")
    spec = _spec(); package = VerificationPackage("verify-uart-v1", spec.spec_id, ("lint",))
    parent = RTLCandidate("candidate-uart-1", spec.spec_id, package.verification_id,
                          "artifact:rtl-uart-1", "rtlscout-v2")
    state.rtl_frontend.add_spec(spec); state.rtl_frontend.add_verification_package(package); state.rtl_frontend.add_candidate(parent)
    monkeypatch.setattr(state, "get_rtl_lineage", lambda *args, **kwargs: state.rtl_frontend.lineage(spec.spec_id))
    attached = state.attach_rtl_simulation_oracle(
        spec.spec_id, {
            "testbench_top": "tb",
            "testbench_source": "module tb; logic clk; wire tx; uart dut(.clk(clk), .tx(tx)); initial begin if (1'b0) $fatal(1, \"check\"); $display(\"PASS\"); end endmodule\n",
            "oracle_origin": "approved_generated", "oracle_reviewed_by": "reviewer-1",
        },
    )
    lineage = state.rtl_frontend.lineage(spec.spec_id)
    child = lineage["candidates"][-1]
    assert child["parent_candidate_ids"] == [parent.candidate_id]
    assert child["provenance"]["testbench_sha256"] == attached["testbench_sha256"]
    assert child["provenance"]["oracle_origin"] == "approved_generated"
    assert state.rtl_frontend.get_verification_package(child["verification_id"]).simulation_oracle_refs


def test_attach_simulation_oracle_cannot_bypass_review_or_self_checking_floor(tmp_path, monkeypatch):
    state = ApiState(tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
                     design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
                     yosys_bin=tmp_path / "missing-yosys", runtime_db_path=tmp_path / "runtime.db")
    spec = _spec(); package = VerificationPackage("verify-uart-v1", spec.spec_id, ("lint",))
    parent = RTLCandidate("candidate-uart-1", spec.spec_id, package.verification_id,
                          "artifact:rtl-uart-1", "rtlscout-v2")
    state.rtl_frontend.add_spec(spec); state.rtl_frontend.add_verification_package(package); state.rtl_frontend.add_candidate(parent)
    monkeypatch.setattr(state, "get_rtl_lineage", lambda *args, **kwargs: state.rtl_frontend.lineage(spec.spec_id))
    with pytest.raises(ValueError, match="self-checking"):
        state.attach_rtl_simulation_oracle(spec.spec_id, {
            "testbench_top": "tb", "testbench_source": "module tb; uart dut(); initial $display(\"PASS\"); endmodule\n",
            "oracle_origin": "approved_generated", "oracle_reviewed_by": "reviewer-1",
        })
    with pytest.raises(ValueError, match="reviewer approval"):
        state.attach_rtl_simulation_oracle(spec.spec_id, {
            "testbench_top": "tb",
            "testbench_source": "module tb; uart dut(); initial begin if (1'b0) $fatal; $display(\"PASS\"); end endmodule\n",
            "oracle_origin": "approved_generated",
        })


def test_terminal_orfs_run_is_written_back_as_candidate_ppa_evidence(tmp_path):
    state = ApiState(tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
                     design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
                     yosys_bin=tmp_path / "missing-yosys", runtime_db_path=tmp_path / "runtime.db")
    spec = _spec(); package = VerificationPackage("verify-uart-v1", spec.spec_id, ("lint",))
    candidate = RTLCandidate("candidate-uart-1", spec.spec_id, package.verification_id,
                             "artifact:rtl-uart-1", "rtlscout-v2")
    state.rtl_frontend.add_spec(spec); state.rtl_frontend.add_verification_package(package); state.rtl_frontend.add_candidate(candidate)
    task = TaskSpec(task_id="orfs-runtime-1", project_id="p", design_id="uart", plugin_id="orfs",
                    inputs={"rtl": {"sha256": "a" * 64}}, labels={"candidate_id": candidate.candidate_id})
    run, stage = state.runtime_store.submit_plugin_run(task, plugin_version="1.0.0")
    workspace = tmp_path / "attempt"; workspace.mkdir()
    attempt = state.runtime_store.start_attempt(stage.stage_run_id, worker_id="test", workspace=workspace, lease_seconds=30)
    report = workspace / "report.json"; report.write_text("{}")
    state.runtime_store.register_artifact(attempt.attempt_id, kind="report", store_key="report.json", size_bytes=2, sha256=hashlib.sha256(b"{}").hexdigest())
    state.runtime_store.finish_attempt(attempt.attempt_id, RuntimeStatus.SUCCEEDED, exit_code=0)
    assert state.record_rtl_implementation_run(run.run_id)["status"] == "passed"
    assert state.rtl_frontend.lineage(spec.spec_id)["checks"][0]["check_kind"] == "ppa"
