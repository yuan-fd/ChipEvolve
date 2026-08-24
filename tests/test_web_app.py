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
    )


def test_health_distinguishes_web_and_execution_readiness(tmp_path):
    state = make_state(tmp_path)

    health = state.health()

    assert health["ok"] is True
    assert health["database_ready"] is True
    assert health["orfs_ready"] is False
    assert health["execution_ready"] is False
    assert health["runtime_worker_ready"] is False
    # BYOK is not a product capability in the internal managed-model service;
    # absence is stronger than a permanently-false compatibility flag.
    assert "byok_input_enabled" not in health
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






def test_api_disables_browser_supplied_provider_credentials_in_internal_mode(tmp_path):
    state = make_state(tmp_path)
    # No provider API, profile/secret store, or fallback method is constructed
    # at all. A browser-supplied key therefore has no application entrypoint.
    assert not hasattr(state, "provider_profiles")
    assert not hasattr(state, "save_provider_profile")
    assert not hasattr(state, "list_provider_profiles")
    assert not hasattr(state, "revoke_provider_secret")




def test_optimization_store_is_research_only_not_a_second_product_api(tmp_path):
    state = make_state(tmp_path)
    study = OptimizationStudy(
        study_id="web-study", design_id="gcd", context_fingerprint="a" * 64,
        parameter_space=(ParameterSpec("core_utilization_pct", 20, 60),),
        objectives=(ObjectiveSpec("area_um2", "min"),), max_runs=8, seed=5,
    )
    state.optimization_store.create(study)
    listed = state.optimization_store.list()
    assert listed[0]["study_id"] == "web-study"
    detail = state.optimization_store.describe("web-study")
    assert detail["prediction_source"] == "predicted"
    assert detail["observation_source"] == "observed"
    assert detail["study"]["max_runs"] == 8
    assert not hasattr(state, "list_optimization_studies")
    assert not hasattr(state, "get_optimization_study")


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


def test_design_circuitops_export_is_rebuildable_and_read_only(tmp_path):
    yosys = which("yosys")
    if yosys is None:
        pytest.skip("Yosys is not installed")
    state = ApiState(
        tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
        design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
        yosys_bin=Path(yosys), runtime_db_path=tmp_path / "runtime.db",
    )
    design = state.designs.import_rtl(
        filename="circuitops_top.v",
        source="module circuitops_top(input a, input b, output y); assign y = a & b; endmodule\n",
    )
    result = state.designs.circuitops_export(design["id"])
    assert result["execution_allowed"] is False
    assert result["manifest"]["source_netlist"]["sha256"]
    assert (state.designs._directory(design["id"]) / "circuitops-v1" / "export_manifest.json").is_file()


def test_design_example_catalog_spans_starter_and_advanced_designs(tmp_path):
    yosys = which("yosys")
    state = ApiState(
        tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
        design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
        yosys_bin=Path(yosys) if yosys else tmp_path / "missing-yosys",
        runtime_db_path=tmp_path / "runtime.db",
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
