import hashlib
from pathlib import Path
from shutil import which

import pytest

from apps.api.app import ApiState
from openroad_platform_contracts import (
    ObjectiveSpec,
    OptimizationStudy,
    ParameterSpec,
    TaskSpec,
)


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
    assert health["byok_input_enabled"] is True


def test_api_byok_is_memory_only_revocable_and_disabled_without_secure_transport(tmp_path):
    state = make_state(tmp_path)
    canary = "api-state-p16-canary"
    created = state.save_provider_profile({
        "owner_id": "alice", "session_id": "browser", "profile_id": "local-fake",
        "base_url": "http://127.0.0.1:12345/v1", "model": "fake",
        "api_key": canary, "allow_private_endpoint": True,
    })
    assert created["api_key"] is None
    assert created["secret"]["secret_present"] is True
    assert canary.encode() not in state.provider_profiles.path.read_bytes()
    assert state.revoke_provider_secret({
        "owner_id": "alice", "session_id": "browser",
        "secret_handle": created["secret"]["handle"],
    }) == {"revoked": True}

    blocked = ApiState(
        tmp_path / "blocked-platform.db", tmp_path / "blocked-uploads",
        tmp_path / "blocked-orfs", design_root=tmp_path / "blocked-designs",
        legacy_root=tmp_path / "blocked-legacy", yosys_bin=Path("/missing/yosys"),
        runtime_db_path=tmp_path / "blocked-runtime.db",
        campaign_db_path=tmp_path / "blocked-campaign.db",
        byok_transport_secure=False,
    )
    with pytest.raises(ValueError, match="HTTPS"):
        blocked.save_provider_profile({
            "base_url": "https://api.openai.com/v1", "model": "fake",
            "api_key": canary,
        })


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

    session = state.create_spec_session({
        "design_id": design["id"], "provider": "deterministic",
        "message": "用 OpenROAD 跑到 GDS，顶层 nl_top，利用率 25%",
    })
    assert session["status"] == "ready"
    submitted = state.execute_spec_session(session["session_id"], {"confirmed": True})
    assert submitted["status"] == "executing"
    assert submitted["runtime"]["run"]["status"] == "queued"
    assert submitted["runtime"]["run"]["task_spec"]["labels"]["spec_session_id"] == session["session_id"]
    repeated = state.execute_spec_session(session["session_id"], {"confirmed": True})
    assert repeated["run_id"] == submitted["run_id"]
    assert len(state.runtime_store.list_runs()) == 1

    campaign = state.create_stage_campaign({
        "design_id": design["id"], "name": "api-grid",
        "parameter_grid": {"core_utilization_pct": [20, 30]},
        "max_parallel": 2, "stage_budgets": {"place": 120},
        "objective_metric": "finish__timing__setup__ws",
    })
    assert len(campaign["members"]) == 2
    assert campaign["stage_policy"]["stage_budgets"] == {"place": 120.0}


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
