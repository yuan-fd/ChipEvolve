"""Bounded projections of authoritative Runtime records for agents and ML."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _physical_report_projection(runtime_view: Mapping[str, Any]) -> dict[str, Any] | None:
    """Project deterministic OpenROAD evidence into bounded AI-readable facts.

    Raw logs, ODB/DEF/GDS and full density arrays remain durable artifacts. A
    planning agent gets typed KPI, stage evidence and diagnosis facts instead.
    """
    envelope = runtime_view.get("analysis_report")
    if not isinstance(envelope, Mapping) or not isinstance(envelope.get("report"), Mapping):
        return None
    report = envelope["report"]
    kpi = report.get("kpi") if isinstance(report.get("kpi"), Mapping) else {}
    allowed = ("instance_count", "area_um2", "utilization_pct", "setup_wns_ns", "setup_tns_ns",
               "hold_wns_ns", "hold_tns_ns", "power_W", "fmax_mhz", "clock_period_ns",
               "wirelength_um", "via_count", "drc_errors", "antenna_violations", "congestion_overflow")
    stages = []
    for name, item in (report.get("stages") or {}).items():
        if isinstance(name, str) and isinstance(item, Mapping):
            metrics = item.get("metrics") if isinstance(item.get("metrics"), Mapping) else {}
            stages.append({"stage": name, "status": item.get("status"),
                           "metrics": {key: metrics[key] for key in allowed if key in metrics}})
    diagnosis = report.get("diagnosis") if isinstance(report.get("diagnosis"), Mapping) else {}
    def messages(key: str) -> list[dict[str, Any]]:
        values = diagnosis.get(key) if isinstance(diagnosis.get(key), list) else []
        return [{field: row.get(field) for field in ("type", "severity", "stage", "message", "recommendation")}
                for row in values[:32] if isinstance(row, Mapping)]
    density = report.get("cell_density") if isinstance(report.get("cell_density"), Mapping) else {}
    return {"kind": "physical_report_ir", "source_artifact_id": envelope.get("source_artifact_id"),
            "source_sha256": envelope.get("source_sha256"), "source_size_bytes": envelope.get("source_size_bytes"),
            "source_url": envelope.get("source_url"), "parser": "openroad-analysis-report-v1",
            "flow_status": report.get("flow_status"), "verdict": report.get("verdict"),
            "runtime_seconds": report.get("runtime_seconds"),
            "kpi": {key: kpi[key] for key in allowed if key in kpi}, "stages": stages,
            "diagnosis": {"summary": diagnosis.get("summary"), "violations": messages("violations"),
                          "observations": messages("observations"),
                          "truncated": len(diagnosis.get("violations") or []) > 32 or len(diagnosis.get("observations") or []) > 32},
            "density_summary": {key: density.get(key) for key in ("available", "method", "grid_size", "total_cells")},
            "raw_artifacts_are_default_hidden": True,
            "raw_artifact_access": "human_or_explicit_bounded_excerpt_only"}


def build_run_evidence_ir(runtime_view: Mapping[str, Any], *, max_metrics: int = 256,
                          max_artifacts: int = 128) -> dict[str, Any]:
    """Turn a RuntimeStore describe view into a bounded, evidence-only IR."""
    run = runtime_view.get("run")
    if not isinstance(run, Mapping) or not isinstance(run.get("run_id"), str):
        raise ValueError("expected authoritative Runtime describe view")
    if not 1 <= max_metrics <= 4096 or not 1 <= max_artifacts <= 4096:
        raise ValueError("RunEvidenceIR bounds are outside policy")
    stages = []
    for stage in runtime_view.get("stages", []):
        attempts = []
        for attempt in stage.get("attempts", []):
            metrics = [{key: item.get(key) for key in ("name", "value", "unit", "parser_id", "parser_version", "context")}
                       for item in attempt.get("metrics", [])[:max_metrics]]
            artifacts = [{key: item.get(key) for key in ("artifact_id", "kind", "store_key", "size_bytes", "sha256")}
                         for item in attempt.get("artifacts", [])[:max_artifacts]]
            attempts.append({"attempt_id": attempt.get("attempt_id"), "status": attempt.get("status"),
                             "exit_code": attempt.get("exit_code"), "failure": attempt.get("failure"),
                             "metrics": metrics, "artifacts": artifacts,
                             "truncation": {"metrics": len(attempt.get("metrics", [])) > len(metrics),
                                            "artifacts": len(attempt.get("artifacts", [])) > len(artifacts)}})
        stages.append({"stage_key": stage.get("stage_key"), "plugin_id": stage.get("plugin_id"),
                       "plugin_version": stage.get("plugin_version"), "status": stage.get("status"),
                       "attempts": attempts})
    task = run.get("task_spec") if isinstance(run.get("task_spec"), Mapping) else {}
    payload = {"schema_version": 1, "kind": "run_evidence_ir", "run": {
        "run_id": run["run_id"], "status": run.get("status"), "plugin_id": task.get("plugin_id"),
        "design_id": task.get("design_id"), "parameters": task.get("parameters", {}),
        "inputs": {key: value for key, value in task.get("inputs", {}).items() if key != "rtl"},
        "rtl_sha256": (task.get("inputs", {}).get("rtl", {}) or {}).get("sha256"),
    }, "stages": stages}
    physical = _physical_report_projection(runtime_view)
    if physical is not None:
        payload["physical_report"] = physical
    payload["fingerprint"] = _digest(payload)
    return payload


def evidence_cards_from_run_ir(run_ir: Mapping[str, Any], *, limit: int = 32) -> list[dict[str, Any]]:
    if run_ir.get("kind") != "run_evidence_ir" or not isinstance(run_ir.get("fingerprint"), str):
        raise ValueError("expected RunEvidenceIR")
    if not 1 <= limit <= 100: raise ValueError("evidence-card limit is outside policy")
    run = run_ir["run"]; ref = f"run:{run['run_id']}"; cards = []
    for stage in run_ir.get("stages", []):
        cards.append({"kind": "stage_status", "claim": f"{stage['stage_key']} status is {stage['status']}",
                      "stage": stage["stage_key"], "evidence_ref": ref, "evidence_sha256": run_ir["fingerprint"],
                      "action_eligible": False})
        for attempt in stage.get("attempts", []):
            for metric in attempt.get("metrics", []):
                if len(cards) >= limit: return cards
                cards.append({"kind": "metric_fact", "claim": f"{metric['name']}={metric['value']} {metric.get('unit') or ''}".strip(),
                              "stage": stage["stage_key"], "evidence_ref": ref,
                              "evidence_sha256": run_ir["fingerprint"], "action_eligible": False})
    physical = run_ir.get("physical_report")
    if isinstance(physical, Mapping):
        for key, value in (physical.get("kpi") or {}).items():
            if len(cards) >= limit: return cards
            cards.append({"kind": "physical_kpi_fact", "claim": f"{key}={value}",
                          "stage": "physical_report", "evidence_ref": ref,
                          "evidence_sha256": run_ir["fingerprint"], "action_eligible": False})
    return cards[:limit]
