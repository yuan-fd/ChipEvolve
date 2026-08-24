import hashlib
import json
import os
import time
from pathlib import Path
from shutil import which

import pytest

from apps.api.app import ApiState
from openroad_platform_contracts import (
    ActionKind, ActionSpec, ObjectiveSpec,
    OptimizationStudy,
    ParameterSpec,
    TaskSpec,
)
from openroad_platform_scheduler.runtime_store import RuntimeStatus


def make_state(tmp_path: Path) -> ApiState:
    return ApiState(
        tmp_path / "platform.db",
        tmp_path / "uploads",
        tmp_path / "orfs",
        design_root=tmp_path / "designs",
        legacy_root=tmp_path / "legacy",
        yosys_bin=Path("/missing/yosys"),
        runtime_db_path=tmp_path / "runtime.db",
        campaign_db_path=tmp_path / "campaign.db",
    )


def test_web_submission_is_persisted_and_can_be_cancelled(tmp_path):
    state = make_state(tmp_path)

    job = state.submit_run({
        "filename": "counter.v",
        "rtl_source": "module counter(input clk, output reg q); always @(posedge clk) q <= ~q; endmodule\n",
        "top": "counter",
        "clock": "clk",
        "target_stage": "route",
    })

    assert job["status"] == "queued"
    assert Path(job["request"]["rtl_path"]).read_text().startswith("module counter")
    assert state.get_run(job["id"])["events"][0]["kind"] == "submitted"
    assert state.cancel_run(job["id"])["status"] == "cancelled"


@pytest.mark.parametrize("filename", ["../escape.v", "design.txt", "bad name.v"])
def test_web_submission_rejects_unsafe_filename(tmp_path, filename):
    state = make_state(tmp_path)

    with pytest.raises(ValueError, match="filename"):
        state.submit_run({"filename": filename, "rtl_source": "module top; endmodule"})


def test_health_distinguishes_web_and_execution_readiness(tmp_path):
    state = make_state(tmp_path)

    health = state.health()

    assert health["ok"] is True
    assert health["database_ready"] is True
    assert health["orfs_ready"] is False
    assert health["execution_ready"] is False
    assert health["runtime_worker_ready"] is False
    assert health["byok_input_enabled"] is True
    assert state.patch_registry.path.is_file()


def test_health_reports_only_a_fresh_live_runtime_worker(tmp_path):
    state = make_state(tmp_path)
    (tmp_path / "runtime-worker.heartbeat.json").write_text(json.dumps({
        "pid": os.getpid(), "status": "idle", "active_run": None,
        "updated_at": "now", "updated_at_epoch": time.time(),
    }))

    health = state.health()

    assert health["runtime_worker_ready"] is True
    assert health["runtime_worker_status"] == "idle"


def test_four_gate_writes_are_scoped_to_the_baseline_owner(tmp_path):
    """An experiment identifier must not become a cross-tenant write capability."""
    state = make_state(tmp_path)
    baseline = TaskSpec(
        "owned-baseline", "project", "design", plugin_id="orfs",
        parameters={"core_utilization_pct": 10.0}, labels={"owner_id": "alice"},
    )
    experiment_id, run_id = state.four_gate.begin_baseline(baseline, producer="alice")
    with pytest.raises(KeyError):
        state.observe_four_gate_run(experiment_id, run_id, owner_id="bob")
    with pytest.raises(KeyError):
        state.propose_four_gate_action(
            experiment_id, {"observation_node_id": "missing", "proposal": {}}, owner_id="bob"
        )


def test_four_gate_decision_indexes_runtime_facts_but_not_reviewer_claims(tmp_path):
    state = make_state(tmp_path)
    task = TaskSpec(
        "learning-baseline", "project", "gcd", plugin_id="orfs",
        inputs={"rtl": {"sha256": "a" * 64}},
        parameters={"platform": "nangate45", "target_stage": "finish",
                    "core_utilization_pct": 10.0},
        labels={"owner_id": "alice"},
    )
    experiment_id, baseline_run = state.four_gate.begin_baseline(task, producer="alice")

    def finish(run_id: str) -> None:
        stage = state.runtime_store.list_stages(run_id)[0]
        attempt = state.runtime_store.start_attempt(
            stage.stage_run_id, worker_id="test", workspace=str(tmp_path / run_id), lease_seconds=30,
        )
        state.runtime_store.finish_attempt(attempt.attempt_id, RuntimeStatus.SUCCEEDED, exit_code=0)

    finish(baseline_run)
    observed = state.observe_four_gate_run(experiment_id, baseline_run, owner_id="alice")["observation_node_id"]
    proposal = state.propose_four_gate_action(experiment_id, {
        "observation_node_id": observed, "proposal": {"source": "test"},
        "evidence_refs": [f"run:{baseline_run}"],
    }, owner_id="alice")["proposal_node_id"]
    action = ActionSpec(
        "learning-action", experiment_id, proposal, ActionKind.PARAMETER,
        "test the declared parameter", "collect a measured result", "one run", "revert parameter",
        {"values": {"core_utilization_pct": 12.0}}, (f"run:{baseline_run}",), "alice",
    )
    submitted = state.review_four_gate_action({"action": action.to_dict()}, owner_id="alice")
    candidate_run = submitted["run"]["run"]["run_id"]
    finish(candidate_run)
    measurement = state.measure_four_gate_attempt(
        experiment_id, submitted["attempt_node_id"], owner_id="alice",
    )["measurement_node_id"]
    decided = state.decide_four_gate_measurement(experiment_id, measurement, {
        "outcome": "no_improvement", "rationale": "The measured run did not improve the objective.",
        "memory_kind": "episodic", "evidence_refs": [f"run:{candidate_run}"],
    }, owner_id="alice")

    assert decided["learning"]["indexed"] is True
    fact_result = state.retrieve_runtime_learning(candidate_run, "Runtime terminal", owner_id="alice")
    decision_result = state.retrieve_runtime_learning(candidate_run, "measured objective", owner_id="alice")
    assert fact_result["bundle"]["records"][0]["knowledge_type"] == "observed_fact"
    assert fact_result["bundle"]["records"][0]["eligible_for_proposal"] is True
    assert decision_result["bundle"]["records"][0]["knowledge_type"] == "failed_attempt"
    assert decision_result["bundle"]["records"][0]["eligible_for_proposal"] is False
    assert fact_result["execution_allowed"] is False
    with pytest.raises(KeyError):
        state.retrieve_runtime_learning(candidate_run, "measured objective", owner_id="bob")


def test_api_disables_browser_supplied_provider_credentials_in_internal_mode(tmp_path):
    state = make_state(tmp_path)
    canary = "api-state-p16-canary"
    with pytest.raises(ValueError, match="disabled in v2 internal mode"):
        state.save_provider_profile({
            "owner_id": "alice", "session_id": "browser", "profile_id": "local-fake",
            "base_url": "http://127.0.0.1:12345/v1", "model": "fake",
            "api_key": canary, "allow_private_endpoint": True,
        })
    assert canary.encode() not in state.provider_profiles.path.read_bytes()


def test_runtime_and_campaign_queries_use_authoritative_store(tmp_path):
    state = make_state(tmp_path)
    task = TaskSpec(task_id="p6-api-task", project_id="p6", design_id="api",
                    plugin_id="echo")
    run, _ = state.runtime_store.submit_plugin_run(task, plugin_version="1.0.0")
    campaign_id = state.campaign_store.create("api-campaign", [task])
    member = state.campaign_store.members(campaign_id)[0]
    state.campaign_store.bind(member.member_id, run.run_id)

    assert state.list_runtime_runs()["runs"][0]["run_id"] == run.run_id
    campaign = state.get_campaign(campaign_id)
    assert campaign["members"][0]["status"] == "queued"
    cancelled = state.cancel_campaign(campaign_id)
    assert cancelled["members"][0]["status"] == "cancelled"


def test_optimization_api_keeps_prediction_and_observation_sources_explicit(tmp_path):
    state = make_state(tmp_path)
    study = OptimizationStudy(
        study_id="web-study", design_id="gcd", context_fingerprint="a" * 64,
        parameter_space=(ParameterSpec("core_utilization_pct", 20, 60),),
        objectives=(ObjectiveSpec("area_um2", "min"),), max_runs=8, seed=5,
    )
    state.optimization_store.create(study)
    listed = state.list_optimization_studies()
    assert listed["studies"][0]["study_id"] == "web-study"
    detail = state.get_optimization_study("web-study")
    assert detail["prediction_source"] == "predicted"
    assert detail["observation_source"] == "observed"
    assert detail["study"]["max_runs"] == 8


def test_taiwei_runtime_view_exposes_hashed_3d_evidence(tmp_path):
    state = make_state(tmp_path)
    task = TaskSpec(
        task_id="p8r-api-task", project_id="p8r", design_id="gcd",
        plugin_id="taiwei-pin-3d",
    )
    run, stage = state.runtime_store.submit_plugin_run(task, plugin_version="1.0.0")
    workspace = tmp_path / "taiwei-attempt"
    workspace.mkdir()
    attempt = state.runtime_store.start_attempt(
        stage.stage_run_id, worker_id="test", workspace=str(workspace), lease_seconds=30
    )
    payloads = {
        "eval.json": ("three_d_eval", '{"finish__route__hb_via__count__phys":{"value":69}}'),
        "toolchain.json": ("toolchain_snapshot", '{"openroad_commit":"305d3ba"}'),
        "tier_view_metrics.json": ("three_d_report", '{"upper_instances":300,"bottom_instances":290}'),
        "final.gds": ("gds", "GDSII"),
        "final.def": ("def", "DEF"),
        "final.odb": ("odb", "ODB"),
        "final.v": ("netlist", "module gcd; endmodule"),
    }
    for name, (kind, content) in payloads.items():
        path = workspace / name
        path.write_text(content, encoding="utf-8")
        state.runtime_store.register_artifact(
            attempt.attempt_id, kind=kind, store_key=name,
            size_bytes=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )

    detail = state.get_runtime_run(run.run_id)
    assert detail["three_d"]["replayable"] is True
    assert detail["three_d"]["tiers"]["upper_instances"] == 300
    artifact = next(item for item in detail["three_d"]["artifacts"] if item["kind"] == "gds")
    path, content_type = state.runtime_artifact(run.run_id, artifact["artifact_id"])
    assert path.read_text() == "GDSII"
    assert content_type == "application/octet-stream"

    path.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        state.runtime_artifact(run.run_id, artifact["artifact_id"])
    assert state.get_runtime_run(run.run_id)["three_d"]["replayable"] is False


def test_runtime_view_exposes_verified_qor_report_and_readable_artifact_titles(tmp_path):
    state = make_state(tmp_path)
    task = TaskSpec(
        task_id="qor-presentation-task", project_id="web", design_id="design-01-deadbeef",
        plugin_id="orfs",
    )
    run, stage = state.runtime_store.submit_plugin_run(task, plugin_version="1.0.0")
    workspace = tmp_path / "qor-attempt"
    (workspace / "orfs/implementation/analysis").mkdir(parents=True)
    (workspace / "orfs/implementation/results/nangate45/gcd/base").mkdir(parents=True)
    attempt = state.runtime_store.start_attempt(
        stage.stage_run_id, worker_id="test", workspace=str(workspace), lease_seconds=30
    )
    payloads = {
        "orfs/implementation/analysis/report.json": (
            "report", json.dumps({
                "design": "gcd", "platform": "nangate45", "verdict": "clean",
                "kpi": {"instance_count": 42, "setup_wns_ns": 0.25, "drc_errors": 0},
                "llm_prompt": "must not be exposed",
            }),
        ),
        "orfs/implementation/results/nangate45/gcd/base/1_synth.odb": ("odb", "SYNTH ODB"),
        "orfs/implementation/results/nangate45/gcd/base/3_place.odb": ("odb", "PLACE ODB"),
        "orfs/implementation/results/nangate45/gcd/base/6_final.def": ("def", "FINAL DEF"),
        "orfs/implementation/results/nangate45/gcd/base/6_final.gds": ("gds", "FINAL GDS"),
    }
    for store_key, (kind, content) in payloads.items():
        path = workspace / store_key
        path.write_text(content, encoding="utf-8")
        state.runtime_store.register_artifact(
            attempt.attempt_id, kind=kind, store_key=store_key,
            size_bytes=path.stat().st_size,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )

    detail = state.get_runtime_run(run.run_id)
    assert detail["analysis_report"]["report"]["kpi"]["instance_count"] == 42
    assert "llm_prompt" not in detail["analysis_report"]["report"]
    artifacts = {
        item["store_key"]: item["presentation"]
        for item in detail["stages"][0]["attempts"][0]["artifacts"]
    }
    assert artifacts[next(key for key in artifacts if key.endswith("1_synth.odb"))]["title_en"] == "Synthesis OpenDB database"
    assert artifacts[next(key for key in artifacts if key.endswith("3_place.odb"))]["title_zh"] == "布局 OpenDB 数据库"
    assert artifacts[next(key for key in artifacts if key.endswith("6_final.def"))]["title_en"] == "Final DEF layout"
    assert artifacts[next(key for key in artifacts if key.endswith("6_final.gds"))]["title_zh"] == "最终 GDSII 版图"

    report_path = workspace / "orfs/implementation/analysis/report.json"
    report_path.write_text("tampered", encoding="utf-8")
    assert "analysis_report" not in state.get_runtime_run(run.run_id)


def test_natural_language_api_returns_preview_without_submitting(tmp_path):
    yosys = which("yosys")
    if yosys is None:
        pytest.skip("Yosys is not installed")
    state = ApiState(
        tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
        design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
        yosys_bin=Path(yosys), runtime_db_path=tmp_path / "runtime.db",
        campaign_db_path=tmp_path / "campaign.db",
    )
    design = state.designs.import_rtl(
        filename="nl_top.v",
        source="module nl_top(input a, output y); assign y = ~a; endmodule\n",
    )
    preview = state.compile_task_intent({
        "design_id": design["id"],
        "intent": "用 OpenROAD Nangate45 跑到 GDS，利用率 30%",
    })
    assert preview["execution_started"] is False
    assert preview["task_spec"]["plugin_id"] == "orfs"
    assert preview["task_spec"]["parameters"]["core_utilization_pct"] == 30.0
    assert state.runtime_store.list_runs() == []

    # v2 internal testing has one server-managed model authority.  The old
    # browser-selectable deterministic provider must not silently survive as a
    # second public RTL/spec path merely to support an offline test.
    with pytest.raises(ValueError, match="platform-managed codex-cli"):
        state.create_spec_session({
            "design_id": design["id"], "provider": "deterministic",
            "message": "用 OpenROAD 跑到 GDS，顶层 nl_top，利用率 25%",
        })
    assert state.runtime_store.list_runs() == []

    campaign = state.create_stage_campaign({
        "design_id": design["id"], "name": "api-grid",
        "platform": "sky130hd",
        "parameter_grid": {"core_utilization_pct": [20, 30]},
        "max_parallel": 2, "stage_budgets": {"place": 120},
        "objective_metric": "finish__timing__setup__ws",
    })
    assert len(campaign["members"]) == 2
    assert campaign["stage_policy"]["stage_budgets"] == {"place": 120.0}
    assert campaign["members"][0]["parameters"]["core_utilization_pct"] == 20
    assert campaign["members"][0]["parameters"]["platform"] == "sky130hd"
    started = state.submit_campaign(campaign["campaign_id"])
    assert started["execution_started"] is True
    assert len(started["run_ids"]) == 2


def test_design_import_creates_netlist_schematic_and_analysis(tmp_path):
    yosys = which("yosys")
    if yosys is None:
        pytest.skip("Yosys is not installed")
    state = ApiState(
        tmp_path / "platform.db",
        tmp_path / "uploads",
        tmp_path / "orfs",
        design_root=tmp_path / "designs",
        legacy_root=tmp_path / "legacy",
        yosys_bin=Path(yosys),
        runtime_db_path=tmp_path / "runtime.db",
        campaign_db_path=tmp_path / "campaign.db",
    )

    design = state.designs.import_rtl(
        filename="xor_gate.v",
        source="module xor_gate(input a, input b, output y); assign y = a ^ b; endmodule\n",
    )

    detail = state.designs.get(design["id"], include_source=True)
    assert detail["module"] == "xor_gate"
    assert detail["analysis"]["instance_count"] > 0
    assert "module xor_gate" in detail["rtl_source"]
    assert "<svg" in state.designs.schematic(design["id"])


def test_design_example_catalog_spans_starter_and_advanced_designs(tmp_path):
    yosys = which("yosys")
    state = ApiState(
        tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
        design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
        yosys_bin=Path(yosys) if yosys else tmp_path / "missing-yosys",
        runtime_db_path=tmp_path / "runtime.db",
        campaign_db_path=tmp_path / "campaign.db",
    )
    examples = state.designs.examples()
    assert {item["id"] for item in examples} >= {
        "adder8", "decoder3to8", "mux4", "counter16",
        "gcd", "alu8", "traffic_controller", "uart_tx", "mini_riscv",
    }
    assert {item["level"] for item in examples} == {"starter", "advanced"}
    assert all("module " in item["rtl_source"] for item in examples)
    if yosys:
        complex_examples = [item for item in examples if item["id"] in {"uart_tx", "mini_riscv"}]
        registered = [state.designs.import_rtl(
            filename=item["filename"], source=item["rtl_source"],
            description=item["description"],
        ) for item in complex_examples]
        assert len(registered) == 2
        assert all(item["analysis"]["instance_count"] > 0 for item in registered)
