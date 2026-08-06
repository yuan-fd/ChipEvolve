from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from openroad_platform_analysis import LearningDatasetStore, RuntimeEvidenceExporter
from openroad_platform_contracts import LearningContext, RuntimeStatus, TaskSpec
from openroad_platform_scheduler import RuntimeStore


RTL_SHA = "a" * 64


def _context(**changes):
    values = dict(
        design_id="gcd", design_fingerprint=RTL_SHA, platform="nangate45",
        pdk_id="nangate45-public", toolchain_id="orfs-51ad123",
        flow_stage="finish", metric_parser_version="orfs-stage-json-1",
    )
    values.update(changes)
    return LearningContext(**values)


def _completed_runtime(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.db")
    task = TaskSpec(
        task_id="learning-task", project_id="p14", design_id="gcd",
        plugin_id="orfs", inputs={"rtl_sha256": RTL_SHA},
        parameters={"platform": "nangate45", "core_utilization_pct": 35.0,
                    "place_density": 0.5}, timeout_seconds=30,
    )
    run, stage = store.submit_plugin_run(task, plugin_version="1.0.0")
    attempt = store.start_attempt(stage.stage_run_id, worker_id="test",
                                  workspace=tmp_path / "workspace", lease_seconds=10)
    artifact_id = store.register_artifact(
        attempt.attempt_id, kind="run_result", store_key="runs/result.json",
        size_bytes=12, sha256="b" * 64,
    )
    store.register_metrics(attempt.attempt_id, [
        {"name": "area_um2", "value": 123.5, "unit": "um2",
         "source_artifact_id": artifact_id, "parser_id": "orfs-stage-json",
         "parser_version": "1", "context": {"source": "observed"}},
        {"name": "note", "value": "not numeric"},
    ])
    store.finish_attempt(attempt.attempt_id, RuntimeStatus.SUCCEEDED, exit_code=0,
                         now=datetime.now(timezone.utc))
    return store, run.run_id


def _runtime_with_qor_artifact(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.db")
    task = TaskSpec(
        task_id="learning-qor", project_id="p14", design_id="gcd", plugin_id="orfs",
        inputs={"rtl": {"sha256": RTL_SHA, "path": "/input/gcd.v", "size_bytes": 1}},
        parameters={"platform": "nangate45", "core_utilization_pct": 37.0,
                    "place_density": 0.52}, timeout_seconds=30,
    )
    run, stage = store.submit_plugin_run(task, plugin_version="1.0.0")
    workspace = tmp_path / "workspace"
    report = workspace / "orfs/implementation/analysis/report.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({
        "runtime_seconds": 81.25,
        "kpi": {"area_um2": 88.3, "setup_wns_ns": 5.6,
                "wirelength_um": 283, "power_W": 8.1e-6, "drc_errors": 0,
                "not_allowlisted": 99},
    }), encoding="utf-8")
    attempt = store.start_attempt(stage.stage_run_id, worker_id="test",
                                  workspace=workspace, lease_seconds=10)
    store.register_artifact(
        attempt.attempt_id, kind="report",
        store_key="orfs/implementation/analysis/report.json",
        size_bytes=report.stat().st_size,
        sha256=hashlib.sha256(report.read_bytes()).hexdigest(),
    )
    store.finish_attempt(attempt.attempt_id, RuntimeStatus.SUCCEEDED, exit_code=0,
                         now=datetime.now(timezone.utc))
    return store, run.run_id, report


def test_runtime_export_is_observed_traceable_and_append_only(tmp_path):
    runtime, run_id = _completed_runtime(tmp_path)
    observation = RuntimeEvidenceExporter(runtime).export_run(run_id, _context())
    assert observation.source == "observed"
    assert observation.metrics == {"area_um2": 123.5}
    assert observation.parameters == {"core_utilization_pct": 35.0,
                                      "place_density": 0.5}
    assert {item.ref.split(":", 1)[0] for item in observation.evidence} == {
        "run", "artifact",
    }
    dataset = LearningDatasetStore(tmp_path / "learning.db")
    dataset.add(observation)
    dataset.add(observation)
    assert dataset.list(context_fingerprint=_context().fingerprint) == [observation]

    changed = dataclasses.replace(observation, metrics={"area_um2": 999.0})
    with pytest.raises(ValueError, match="conflicts"):
        dataset.add(changed)


def test_runtime_export_rejects_design_platform_and_rtl_context_mismatch(tmp_path):
    runtime, run_id = _completed_runtime(tmp_path)
    exporter = RuntimeEvidenceExporter(runtime)
    with pytest.raises(ValueError, match="design"):
        exporter.export_run(run_id, _context(design_id="aes"))
    with pytest.raises(ValueError, match="RTL fingerprint"):
        exporter.export_run(run_id, _context(design_fingerprint="c" * 64))
    with pytest.raises(ValueError, match="platform"):
        exporter.export_run(run_id, _context(platform="asap7"))


def test_runtime_export_reads_only_verified_registered_orfs_qor(tmp_path):
    runtime, run_id, _ = _runtime_with_qor_artifact(tmp_path)
    observation = RuntimeEvidenceExporter(runtime).export_run(run_id, _context())
    assert observation.metrics == {
        "area_um2": 88.3, "setup_wns_ns": 5.6, "wirelength_um": 283.0,
        "power_W": 8.1e-6, "drc_errors": 0.0, "runtime_seconds": 81.25,
    }
    assert "not_allowlisted" not in observation.metrics
    assert observation.metric_units["power_W"] == "W"


def test_runtime_export_rejects_tampered_registered_qor(tmp_path):
    runtime, run_id, report = _runtime_with_qor_artifact(tmp_path)
    report.write_text('{"kpi":{"area_um2":999}}', encoding="utf-8")
    with pytest.raises(ValueError, match="mismatch"):
        RuntimeEvidenceExporter(runtime).export_run(run_id, _context())


def test_runtime_export_ignores_unregistered_qor_file(tmp_path):
    runtime, run_id = _completed_runtime(tmp_path)
    attempts = [attempt for stage in runtime.list_stages(run_id)
                for attempt in runtime.list_attempts(stage.stage_run_id)]
    report = Path(attempts[0].workspace) / "orfs/implementation/analysis/report.json"
    report.parent.mkdir(parents=True)
    report.write_text('{"kpi":{"wirelength_um":1}}', encoding="utf-8")
    observation = RuntimeEvidenceExporter(runtime).export_run(run_id, _context())
    assert "wirelength_um" not in observation.metrics
