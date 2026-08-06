from __future__ import annotations

import json
import platform
import sys

import pytest

from openroad_platform_analysis import (
    OptimizationStudyStore,
    RuntimeEvidenceExporter,
)
from openroad_platform_contracts import (
    ExperimentCandidate,
    ExperimentPlan,
    LearningContext,
    ObjectiveSpec,
    OptimizationStudy,
    ParameterSpec,
    PluginManifest,
    TaskSpec,
)
from openroad_platform_execution import PluginRegistry
from openroad_platform_scheduler import (
    CampaignStore,
    OptimizationCampaignBridge,
    RuntimeStore,
    StageAwareCampaignManager,
    WorkflowRuntime,
)


ADAPTER = r'''import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument("--request", type=Path); p.add_argument("--result", type=Path)
a=p.parse_args(); task=json.loads(a.request.read_text())["task"]
started=datetime.now(timezone.utc).isoformat(); print("[orfs-stage-start] synth", flush=True)
report=a.result.parent/"report.json"; report.write_text(json.dumps({"task":task["task_id"]}))
score=float(task["parameters"]["core_utilization_pct"])
print("[orfs-stage] synth succeeded 0.01s", flush=True)
a.result.write_text(json.dumps({"schema_version":1,"status":"succeeded","exit_code":0,
 "started_at":started,"ended_at":datetime.now(timezone.utc).isoformat(),
 "metrics":[{"name":"score","value":score,"unit":"points","parser_id":"fixture","parser_version":"1"}],
 "artifacts":[{"kind":"report","path":"report.json"}],"failure":None,"provenance":{"fixture":True}}))
'''


def _context():
    return LearningContext(
        design_id="gcd", design_fingerprint="a" * 64, platform="nangate45",
        pdk_id="nangate45-public", toolchain_id="orfs-test",
        flow_stage="finish", metric_parser_version="fixture-1",
    )


def _manager(tmp_path):
    adapter = tmp_path / "adapter.py"
    adapter.write_text(ADAPTER)
    manifest = PluginManifest(
        plugin_id="orfs", plugin_version="1.0.0",
        adapter_entry=(sys.executable, str(adapter)), capabilities=("eda.orfs",),
        supported_arch=(platform.machine(),), input_schema={"type": "object"},
        output_schema={"type": "object"}, artifact_rules=({"kind": "report", "required": True},),
        default_timeout_seconds=10,
    )
    runtime = WorkflowRuntime(
        RuntimeStore(tmp_path / "runtime.db"), PluginRegistry([manifest]),
        workspace_root=tmp_path / "runs", lease_seconds=2,
    )
    return StageAwareCampaignManager(CampaignStore(tmp_path / "campaign.db"), runtime)


def _task():
    return TaskSpec(
        task_id="base", project_id="p14", design_id="gcd", plugin_id="orfs",
        inputs={"rtl_sha256": "a" * 64},
        parameters={"platform": "nangate45", "core_utilization_pct": 30.0},
        timeout_seconds=10,
    )


def _plan(**parameter_changes):
    return ExperimentPlan(
        plan_id="plan-1", producer="p14-bo-gp", design_id="gcd",
        platform="openroad", baseline_parameters={},
        candidates=(ExperimentCandidate(
            candidate_id="candidate-1",
            parameters={"core_utilization_pct": 35.0, **parameter_changes},
            source_trial_id="optimizer-1", evidence_refs=("source:study:study-1",),
        ),), max_child_runs=1,
        provenance={"predictions_are_canonical_metrics": False, "study_id": "study-1"},
    )


def _study():
    return OptimizationStudy(
        study_id="study-1", design_id="gcd", context_fingerprint=_context().fingerprint,
        parameter_space=(ParameterSpec("core_utilization_pct", 20, 60),),
        objectives=(ObjectiveSpec("score", "min"),), max_runs=4, seed=3,
    )


def test_bridge_is_idempotent_runtime_authoritative_and_ingests_observed(tmp_path):
    manager = _manager(tmp_path)
    bridge = OptimizationCampaignBridge(manager)
    campaign_id = bridge.create("optimizer", _task(), _plan(), objective_metric="score")
    assert bridge.create("optimizer", _task(), _plan(), objective_metric="score") == campaign_id
    member = manager.store.members(campaign_id)[0]
    assert member.run_id is None
    assert member.task_spec.labels["prediction_source"] == "predicted-not-canonical"

    view = manager.run_until_terminal(campaign_id, timeout_seconds=10)
    assert view["counts"] == {"succeeded": 1}
    study_store = OptimizationStudyStore(tmp_path / "study.db")
    study_store.create(_study())
    ids = bridge.ingest_terminal(
        campaign_id, context=_context(), exporter=RuntimeEvidenceExporter(manager.runtime.store),
        study_store=study_store, study_id="study-1",
    )
    assert len(ids) == 1
    observation = study_store.observations("study-1")[0]
    assert observation.source == "observed"
    assert observation.metrics["score"] == 35.0
    assert all(metric["name"] != "predicted_score"
               for metric in manager.runtime.store.metrics(observation.attempt_id))


def test_bridge_rejects_unbounded_or_prediction_ambiguous_plan(tmp_path):
    bridge = OptimizationCampaignBridge(_manager(tmp_path))
    with pytest.raises(ValueError, match="unsupported"):
        bridge.create("bad", _task(), _plan(arbitrary_tcl=1.0))
    ambiguous = _plan()
    ambiguous = ExperimentPlan.from_dict({
        **ambiguous.to_dict(), "provenance": {"predictions_are_canonical_metrics": True},
    })
    with pytest.raises(ValueError, match="isolate predicted"):
        bridge.create("bad", _task(), ambiguous)
