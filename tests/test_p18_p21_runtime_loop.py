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
        optimization_db_path=tmp_path / "optimization.db",
    )
