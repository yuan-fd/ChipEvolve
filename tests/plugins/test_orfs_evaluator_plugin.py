"""The ORFS evaluator plugin, exercised through the real protected boundary.

This is the test that proves the knowledge port is wired up rather than merely
present.  It builds an ORFS-shaped workspace, runs the plugin as a separate
process through ``PluginBackedEvaluator``, and checks the verdict the platform
would actually store.

It is also the test that would catch a regression in the time-unit conversion
at the integration level: a picosecond run must still be admissible when its
converted slack is positive.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openroad_platform_contracts import (
    EvaluationRequest,
    PluginManifest,
    PROTECTED_EVALUATOR_CAPABILITY,
    TaskSpec,
    VerdictStatus,
)
from openroad_platform_evaluator import PluginBackedEvaluator
from openroad_platform_registry import PluginRegistry
from openroad_platform_runtime import ProcessAdapter
from openroad_platform_runtime.guardian import ProcessGuardian

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGINS_ROOT = REPO_ROOT / "plugins"
ADMISSIONS_ROOT = REPO_ROOT / "admissions"
PLATFORM = "sky130hd"
DESIGN = "gcd"


def build_workspace(
    workspace: Path, *, time_unit: str = "1ns", scale: float = 1.0,
    setup_ws: float = 0.1, drc: int = 0,
) -> Path:
    """An ORFS-shaped attempt workspace, as the execution adapter leaves it.

    The files sit at the workspace root because that is where the adapter writes:
    the kernel hands the evaluator the attempt workspace, and the adapter owns
    its root.  An earlier version of this fixture nested everything under
    ``orfs/implementation``, which nothing in v2 creates -- so the fixture passed
    while every real evaluation abstained.
    """
    implementation = workspace
    logs = implementation / "logs" / PLATFORM / DESIGN / "base"
    results = implementation / "results" / PLATFORM / DESIGN / "base"
    logs.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)

    def write(path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload), encoding="utf-8")

    write(logs / "1_1_yosys.json", {
        "synth__design__instance__count": 4200,
        "synth__design__instance__area": 12345.6789,
        "run__flow__platform__time_units": time_unit,
    })
    write(logs / "2_1_floorplan.json", {
        "floorplan__design__die__area": 250000.0,
        "floorplan__design__core__area": 200000.0,
        "floorplan__design__instance__utilization": 0.6,
    })
    write(logs / "3_5_place_dp.json", {
        "detailedplace__design__instance__count": 4200,
        "detailedplace__design__instance__area": 12345.6789,
    })
    write(logs / "4_1_cts.json", {
        "cts__clock__skew__worst": 0.03 * scale,
    })
    write(logs / "5_2_route.json", {
        "detailedroute__route__wirelength": 456789,
        "detailedroute__route__drc_errors": drc,
        "detailedroute__timing__setup__ws": setup_ws * scale,
    })
    write(logs / "6_report.json", {
        "finish__design__instance__count": 4200,
        "finish__design__instance__area": 12345.6789,
        "finish__design__die__area": 250000.0,
        "finish__design__core__area": 200000.0,
        "finish__design__instance__utilization": 0.62,
        "finish__timing__setup__ws": setup_ws * scale,
        "finish__timing__hold__ws": 0.05 * scale,
        "finish__power__total": 0.0123,
    })

    for name in ("6_final.odb", "6_final.def", "6_final.gds", "6_final.v"):
        (results / name).write_text(f"content of {name}\n", encoding="utf-8")

    (implementation / "design_input_manifest.json").write_text(
        '{"files": []}\n', encoding="utf-8")
    (implementation / "config.mk").write_text(
        "PLATFORM = sky130hd\n", encoding="utf-8")

    write(implementation / "plan.json", {
        "run_id": "run-1",
        "design": DESIGN,
        "config_path": "config.mk",
        "request": {
            "platform": PLATFORM,
            "or_seed": 1,
            "target_stage": "finish",
            "clock_period_ns": 2.0,
        },
    })
    write(implementation / "run_result.json", {
        "stages": [
            {"stage": "synth", "status": "succeeded", "returncode": 0, "seconds": 10.0},
            {"stage": "finish", "status": "succeeded", "returncode": 0, "seconds": 90.0},
        ],
    })
    return workspace


def evaluator_from_registry(tmp_path: Path) -> PluginBackedEvaluator:
    registry = PluginRegistry.from_directory(PLUGINS_ROOT, admissions_root=ADMISSIONS_ROOT)
    manifest = registry.resolve("orfs-evaluator",
                                capability=PROTECTED_EVALUATOR_CAPABILITY)
    return PluginBackedEvaluator(
        adapter=ProcessAdapter(
            ProcessGuardian(poll_interval=0.02, terminate_grace=1.0)
        ),
        manifest=manifest,
    )


def request_for(workspace: Path) -> EvaluationRequest:
    return EvaluationRequest(
        manifest=PluginManifest(
            plugin_id="orfs", plugin_version="1.0.0",
            adapter_entry=("python3", "./a.py"),
            capabilities=("eda.orfs",),
            supported_arch=("aarch64", "x86_64", "arm64"),
            ),
        task=TaskSpec(
            task_id="orfs-run-1", project_id="p", design_id=DESIGN,
            plugin_id="orfs",
            # The kernel tells the evaluator which workspace it is evaluating;
            # the adapter reads this rather than guessing from its own argv.
            inputs={"workspace": str(workspace)}, labels={},
        ),
        workspace=str(workspace),
        attempt_id="attempt-1",
    )


# --------------------------------------------------------------------------
# the plugin is a real, admitted capability
# --------------------------------------------------------------------------

def test_the_evaluator_plugin_is_discovered_and_admitted():
    registry = PluginRegistry.from_directory(PLUGINS_ROOT, admissions_root=ADMISSIONS_ROOT)
    plugin = registry.get("orfs-evaluator")
    assert plugin.executable is True
    assert PROTECTED_EVALUATOR_CAPABILITY in plugin.manifest.capabilities
    assert Path(plugin.adapter_entry[1]).parent.name == "orfs-evaluator"


def test_the_plugin_cannot_be_used_to_execute_eda(tmp_path):
    """It declares exactly one capability, and it is not an execution one."""
    registry = PluginRegistry.from_directory(PLUGINS_ROOT, admissions_root=ADMISSIONS_ROOT)
    with pytest.raises(Exception, match="lacks capability"):
        registry.resolve("orfs-evaluator", capability="eda.rtl_to_gds")


# --------------------------------------------------------------------------
# an admissible run
# --------------------------------------------------------------------------

def test_a_clean_run_produces_an_admissible_verdict(tmp_path):
    workspace = build_workspace(tmp_path / "workspace")
    outcome = evaluator_from_registry(tmp_path).evaluate_with_evidence(
        request_for(workspace)
    )
    assert outcome.verdict.status is VerdictStatus.ADMISSIBLE
    metrics = {m.name: m.value for m in outcome.verdict.metrics}
    assert metrics["setup_wns_ns"] == pytest.approx(0.1)
    assert metrics["drc_errors"] == 0
    assert metrics["instance_area_um2"] == pytest.approx(12345.6789)
    assert metrics["power_W"] == pytest.approx(0.0123)
    # Cross-check: the runtime summary the plugin derived from the stage list.
    assert metrics["runtime_seconds"] == pytest.approx(100.0)


def test_every_metric_traces_to_a_hashed_artifact(tmp_path):
    workspace = build_workspace(tmp_path / "workspace")
    outcome = evaluator_from_registry(tmp_path).evaluate_with_evidence(
        request_for(workspace)
    )
    assert (workspace / "common_evaluation.json").is_file()
    for metric in outcome.verdict.metrics:
        assert metric.context["source_artifact_store_key"] == "common_evaluation.json"
    assert len(outcome.verdict_sha256) == 64


def test_a_picosecond_run_is_admissible_when_seen_in_nanoseconds(tmp_path):
    """ASAP7 reports in ps.  A 0.1 ns slack arrives as 100.0 and must be scored
    as 0.1, not as a 100 ns failure."""
    workspace = build_workspace(tmp_path / "workspace", time_unit="1ps",
                                scale=1000.0, setup_ws=0.1)
    outcome = evaluator_from_registry(tmp_path).evaluate_with_evidence(
        request_for(workspace)
    )
    assert outcome.verdict.status is VerdictStatus.ADMISSIBLE
    metrics = {m.name: m.value for m in outcome.verdict.metrics}
    assert metrics["setup_wns_ns"] == pytest.approx(0.1)


# --------------------------------------------------------------------------
# a run that must not be stored as a result
# --------------------------------------------------------------------------

def test_a_timing_violation_is_rejected_with_its_reason(tmp_path):
    workspace = build_workspace(tmp_path / "workspace", setup_ws=-0.05)
    outcome = evaluator_from_registry(tmp_path).evaluate_with_evidence(
        request_for(workspace)
    )
    assert outcome.verdict.status is VerdictStatus.REJECTED
    assert "setup_timing_violation" in outcome.verdict.reason
    # The reasons travel with the verdict, so a later reader does not have to
    # re-derive why the run was refused.
    assert "setup_timing_violation" in \
        outcome.verdict.loss_manifest["gate_reasons"]


def test_a_nonzero_drc_is_rejected(tmp_path):
    workspace = build_workspace(tmp_path / "workspace", drc=4)
    outcome = evaluator_from_registry(tmp_path).evaluate_with_evidence(
        request_for(workspace)
    )
    assert outcome.verdict.status is VerdictStatus.REJECTED
    assert "drc_violation" in outcome.verdict.reason


def test_a_missing_final_artifact_is_rejected(tmp_path):
    workspace = build_workspace(tmp_path / "workspace")
    (workspace / "results" / PLATFORM / DESIGN / "base" / "6_final.gds").unlink()
    outcome = evaluator_from_registry(tmp_path).evaluate_with_evidence(
        request_for(workspace)
    )
    assert outcome.verdict.status is VerdictStatus.REJECTED
    assert "missing_final_artifacts" in outcome.verdict.reason


def test_a_workspace_with_no_orfs_evidence_abstains_rather_than_guessing(tmp_path):
    """'We could not evaluate this' is a different answer from 'it failed'."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outcome = evaluator_from_registry(tmp_path).evaluate_with_evidence(
        request_for(workspace)
    )
    assert outcome.verdict.status is VerdictStatus.INCOMPLETE
    assert "no ORFS attempt under" in outcome.verdict.reason
    assert "plan.json is missing" in outcome.verdict.reason


# --------------------------------------------------------------------------
# provenance
# --------------------------------------------------------------------------

def test_the_verdict_records_which_evaluator_produced_it(tmp_path):
    workspace = build_workspace(tmp_path / "workspace")
    outcome = evaluator_from_registry(tmp_path).evaluate_with_evidence(
        request_for(workspace)
    )
    assert outcome.pin.plugin_id == "orfs-evaluator"
    assert outcome.pin.plugin_version == "1.0.0"
    assert len(outcome.pin.manifest_sha256) == 64
