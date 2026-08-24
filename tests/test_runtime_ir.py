from __future__ import annotations

from openroad_platform_analysis import build_run_evidence_ir, evidence_cards_from_run_ir


def test_runtime_ir_is_bounded_and_tracks_authoritative_artifacts():
    view = {"run": {"run_id": "run-1", "status": "succeeded", "task_spec": {
        "plugin_id": "orfs", "design_id": "gcd", "parameters": {"place_density": .6},
        "inputs": {"rtl": {"sha256": "a" * 64}, "clock": "clk"}}}, "stages": [{
        "stage_key": "plugin", "plugin_id": "orfs", "plugin_version": "1", "status": "succeeded",
        "attempts": [{"attempt_id": "a1", "status": "succeeded", "exit_code": 0, "failure": None,
                      "metrics": [{"name": "area", "value": 12, "unit": "um2"}],
                      "artifacts": [{"artifact_id": "x", "kind": "gds", "store_key": "x.gds", "size_bytes": 3, "sha256": "b" * 64}]}]}]}
    ir = build_run_evidence_ir(view)
    assert ir["run"]["rtl_sha256"] == "a" * 64
    assert ir["stages"][0]["attempts"][0]["artifacts"][0]["kind"] == "gds"
    assert evidence_cards_from_run_ir(ir)[1]["kind"] == "metric_fact"


def test_runtime_ir_projects_openroad_report_without_feeding_raw_log_or_density_array():
    view = {"run": {"run_id": "run-2", "status": "succeeded", "task_spec": {
        "plugin_id": "orfs", "design_id": "gcd", "parameters": {}, "inputs": {}}}, "stages": [],
        "analysis_report": {"source_artifact_id": "report-1", "source_sha256": "c" * 64,
        "source_size_bytes": 1234, "report": {
            "flow_status": "completed", "verdict": "needs_improvement", "runtime_seconds": 12.3,
            "kpi": {"area_um2": 123.4, "setup_wns_ns": -0.2, "power_W": 0.01, "drc_errors": 0},
            "stages": {"route": {"status": "completed", "metrics": {"drc_errors": 0, "wirelength_um": 20}}},
            "diagnosis": {"summary": "timing violation", "violations": [{"type": "setup", "message": "WNS negative", "evidence": {"huge": "hidden"}}]},
            "cell_density": {"available": True, "grid_size": 50, "density_map": [[0.1] * 50] * 50},
        }}}
    ir = build_run_evidence_ir(view)
    physical = ir["physical_report"]
    assert physical["kpi"]["area_um2"] == 123.4
    assert physical["source_sha256"] == "c" * 64
    assert physical["diagnosis"]["violations"][0]["message"] == "WNS negative"
    assert "density_map" not in physical["density_summary"]
    assert physical["raw_artifacts_are_default_hidden"] is True
    assert physical["raw_artifact_access"] == "human_or_explicit_bounded_excerpt_only"
    assert any(card["kind"] == "physical_kpi_fact" for card in evidence_cards_from_run_ir(ir, limit=20))
