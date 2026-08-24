import hashlib
from types import SimpleNamespace

import pytest

from openroad_platform_analysis import agent_evidence_view, build_edair, evidence_packet, physical_ir, timing_ir
from apps.api.app import ApiState


def _source():
    return {"artifact_id": "artifact-report", "sha256": hashlib.sha256(b"report").hexdigest(),
            "kind": "report", "parser": "test", "parser_version": "1", "source_size_bytes": 6}


def test_edair_keeps_normalized_facts_linked_to_raw_artifacts():
    source = _source()
    timing = timing_ir([{"path_id": "p0", "path_type": "setup", "slack_ns": -0.1,
                         "points": [{"instance": "u0", "increment_ns": .2}]}], source=source)
    physical = physical_ir(instances=[{"name": "u0", "x": 1, "y": 2}], nets=[],
                           violations=[{"rule": "M1 spacing", "x": 2, "y": 3}], source=source)
    run = {"kind": "run_evidence_ir", "fingerprint": "a" * 64}
    edair = build_edair(design=None, run=run, timing=timing, physical=physical, raw_artifacts=[source])
    view = agent_evidence_view(edair)
    assert edair["raw_artifacts"][0]["sha256"] == source["sha256"]
    assert edair["fidelity_manifest"]["timing"]["paths"] == 1
    assert edair["fidelity_manifest"]["physical"]["violations"] == 1
    assert edair["fidelity_manifest"]["detail_recovery"] == "hash_checked_bounded_excerpt"
    assert view["facts"][0]["evidence"]["artifact_id"] == "artifact-report"
    assert view["execution_allowed"] is False
    packet = evidence_packet(edair, focus="diagnosis", max_items=1)
    assert packet["facts"][0]["kind"] == "timing_path"
    assert packet["loss_manifest"]["raw_artifacts_not_inlined"] == 1
    assert packet["execution_allowed"] is False


def test_diagnosis_packet_includes_attributed_qor_and_declares_omitted_stage_detail():
    source = _source()
    run = {"kind": "run_evidence_ir", "fingerprint": "a" * 64,
           "physical_report": {
               "source_artifact_id": source["artifact_id"],
               "source_sha256": source["sha256"], "source_size_bytes": 6,
               "parser": "fixture-report", "kpi": {"area_um2": 12.5, "drc_errors": 0},
               "stages": [{"stage": "route", "metrics": {"wirelength_um": 99}}],
               "diagnosis": {"observations": [{"type": "timing_clean", "stage": "finish",
                                                  "message": "setup converged"}]}}}
    edair = build_edair(design=None, run=run, raw_artifacts=[source])
    packet = evidence_packet(edair, focus="diagnosis", max_items=2)
    assert [item["kind"] for item in packet["facts"]] == ["qor_metric", "qor_metric"]
    assert all(item["evidence"]["artifact_id"] == "artifact-report"
               for item in packet["facts"])
    assert packet["loss_manifest"]["stage_metrics"] == 1
    assert packet["loss_manifest"]["diagnosis_observations"] == 1


def test_physical_ir_preserves_per_object_sources_when_def_and_reports_are_joined():
    placement = _source()
    report = {**_source(), "artifact_id": "artifact-drc",
              "sha256": hashlib.sha256(b"drc").hexdigest()}
    physical = physical_ir(
        instances=[{"name": "u0", "x": 1, "y": 2, "evidence": placement}],
        nets=[], violations=[{"rule": "M1 spacing", "evidence": report}],
        source=placement)
    assert physical["instances"][0]["evidence"]["artifact_id"] == "artifact-report"
    assert physical["violations"][0]["evidence"]["artifact_id"] == "artifact-drc"


def test_hash_checked_artifact_excerpt_recovers_omitted_raw_detail(tmp_path):
    source = tmp_path / "timing.rpt"
    source.write_text("header\npath-0 slack -0.12\npath-1 slack 0.04\n", encoding="utf-8")
    dummy = SimpleNamespace(
        runtime_artifact=lambda *_args, **_kwargs: (source, "text/plain"))
    first = ApiState.runtime_artifact_excerpt(
        dummy, "run-1", "artifact-1", offset=7, length=20)
    second = ApiState.runtime_artifact_excerpt(
        dummy, "run-1", "artifact-1", offset=first["end_offset"], length=20)
    assert first["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert first["content"].startswith("path-0")
    assert first["loss_manifest"]["bytes_after"] > 0
    assert second["offset"] == first["end_offset"]
    assert first["execution_allowed"] is False

    binary = tmp_path / "layout.gds"
    binary.write_bytes(b"\x00\x01\x02")
    dummy.runtime_artifact = lambda *_args, **_kwargs: (binary, "application/octet-stream")
    with pytest.raises(ValueError, match="binary"):
        ApiState.runtime_artifact_excerpt(dummy, "run-1", "artifact-2")
