from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path

from apps.api.app import ApiState
from openroad_platform_analysis import MultiObjectiveBayesianOptimizer, build_recommendation
from openroad_platform_contracts import (
    EvidencePointer, LearningContext, LearningObservation, ObjectiveSpec,
    OptimizationStudy, ParameterSpec, RuntimeStatus,
)
from openroad_platform_execution import (
    ProcessAdapter, build_edacraft_task, edacraft_plugin_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".external-src" / "edacraft"


def test_p18_real_solver_smokes_are_runtime_adapter_results(tmp_path):
    expected = {
        "cktcraft": {"cktcraft.v_mid": 4.2},
        "momcraft": {"momcraft.s21_magnitude": None},
    }
    for slug, metric_expectations in expected.items():
        execution = ProcessAdapter().execute(
            edacraft_plugin_manifest(slug, SOURCE, sys.executable),
            build_edacraft_task(slug, task_id=f"p18-{slug}"),
            workspace=tmp_path / slug,
        )
        assert execution.result.status is RuntimeStatus.SUCCEEDED
        metrics = {item["name"]: item["value"] for item in execution.result.metrics}
        for name, value in metric_expectations.items():
            assert name in metrics
            if value is not None:
                assert abs(metrics[name] - value) < 1e-9
        report = json.loads((tmp_path / slug / "capability_report.json").read_text())
        assert report["safety"]["full_solver_executed"] is True
        assert report["safety"]["signoff_claimed"] is False


def _state(tmp_path: Path) -> ApiState:
    return ApiState(
        tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
        design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
        yosys_bin=ROOT.parent / "bin" / "yosys",
        runtime_db_path=tmp_path / "runtime.db",
        campaign_db_path=tmp_path / "campaign.db",
        optimization_db_path=tmp_path / "optimization.db",
    )


def test_p21_human_decision_creates_campaign_then_explicitly_queues_runtime(tmp_path):
    state = _state(tmp_path)
    rtl_source = "module p21_top(input clk, input a, output reg y); always @(posedge clk) y <= a; endmodule\n"
    design = state.designs.import_rtl(
        filename="p21_top.v",
        source=rtl_source,
    )
    context = LearningContext(
        design_id=design["id"],
        design_fingerprint=hashlib.sha256(rtl_source.encode()).hexdigest(), platform="nangate45",
        pdk_id="nangate45-public", toolchain_id="orfs-p21", flow_stage="finish",
        metric_parser_version="orfs-stage-json-1",
    )
    study = OptimizationStudy(
        study_id="p21-study", design_id=design["id"], context_fingerprint=context.fingerprint,
        parameter_space=(ParameterSpec("core_utilization_pct", 20, 60),),
        objectives=(ObjectiveSpec("area_um2", "min"),), max_runs=8, seed=21,
    )
    state.optimization_store.create(study)
    for index, util in enumerate((25.0, 35.0, 45.0)):
        state.optimization_store.add_observation(study.study_id, LearningObservation(
            observation_id=f"p21-observation-{index}", context=context,
            parameters={"core_utilization_pct": util},
            metrics={"area_um2": 150.0 - index * 12}, metric_units={"area_um2": "um2"},
            status="succeeded", cost_seconds=10, run_id=f"historical-run-{index}",
            attempt_id=f"historical-attempt-{index}",
            evidence=(EvidencePointer(f"artifact:p21-{index}", f"{index + 1}" * 64),),
        ))
    proposal = MultiObjectiveBayesianOptimizer(pool_size=64).propose(
        study, state.optimization_store.observations(study.study_id)
    )
    state.optimization_store.save_proposal(proposal)
    recommendation = build_recommendation(
        study, proposal, state.optimization_store.observations(study.study_id)
    )
    state.recommendation_store.save("local-user", recommendation)

    approved = state.decide_recommendation(recommendation.recommendation_id, {
        "owner_id": "local-user", "action": "accepted", "create_campaign": True,
    })
    assert approved["campaign_created"] is True
    assert approved["execution_started"] is False
    assert state.runtime_store.list_runs() == []
    assert approved["experiment_plan"]["provenance"]["human_confirmed"] is True

    submitted = state.decide_recommendation(recommendation.recommendation_id, {
        "owner_id": "local-user", "action": "accepted", "create_campaign": True,
        "submit": True,
    })
    assert submitted["campaign_id"] == approved["campaign_id"]
    assert submitted["execution_started"] is True
    assert len(state.runtime_store.list_runs()) == 1
    run = state.runtime_store.list_runs()[0]
    assert run.status is RuntimeStatus.QUEUED
    assert run.task_spec.labels["human_decision_id"] == approved["decision"]["decision_id"]

    stage = state.runtime_store.list_stages(run.run_id)[0]
    workspace = tmp_path / "verified-runtime-attempt"
    workspace.mkdir()
    attempt = state.runtime_store.start_attempt(
        stage.stage_run_id, worker_id="p21-test-worker", workspace=str(workspace),
        lease_seconds=30,
    )
    state.runtime_store.register_metrics(attempt.attempt_id, [{
        "name": "area_um2", "value": 111.0, "unit": "um2",
        "parser_id": "p21-fixture", "parser_version": "1",
    }])
    state.runtime_store.finish_attempt(
        attempt.attempt_id, RuntimeStatus.SUCCEEDED, exit_code=0,
    )
    collected = state.collect_campaign_learning(submitted["campaign_id"], {
        "study_id": study.study_id, "design_fingerprint": context.design_fingerprint,
        "platform": context.platform, "pdk_id": context.pdk_id,
        "toolchain_id": context.toolchain_id, "flow_stage": context.flow_stage,
        "metric_parser_version": context.metric_parser_version,
        "tenant_id": "local-user", "project_id": "openroad-platform",
    })
    assert len(collected["observation_ids"]) == 1
    assert collected["source"] == "verified-runtime-observed"
    learned = state.optimization_store.observations(study.study_id)[-1]
    assert learned.metrics["area_um2"] == 111.0
    assert learned.source == "observed"
