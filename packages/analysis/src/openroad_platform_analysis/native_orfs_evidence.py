"""Verified adapters for evidence produced by the native ORFS command-line flow.

The native ``openroad_platform_execution.cli`` runner deliberately writes its
own evidence directory instead of a Runtime database row.  This adapter does
not pretend otherwise: it validates the immutable files in that directory and
returns a Runtime-*shaped view solely for read-only replication/causal
analysis.  No task can be submitted from this module.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


_REPORT_METRICS = {
    "finish__design__instance__area": "area_um2",
    "finish__timing__setup__ws": "setup_wns_ns",
    "finish__power__total": "power_W",
}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON evidence: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON evidence must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_map(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("native result has no artifact list")
    mapped: dict[str, dict[str, Any]] = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
            raise ValueError("native result has malformed artifact")
        mapped[artifact["path"]] = artifact
    return mapped


def _verify_artifact(root: Path, artifacts: dict[str, dict[str, Any]], relative: str) -> None:
    item = artifacts.get(relative)
    path = root / relative
    if item is None or not path.is_file():
        raise ValueError(f"required native evidence artifact is absent: {relative}")
    if item.get("size_bytes") != path.stat().st_size:
        raise ValueError(f"native evidence artifact size mismatch: {relative}")
    digest = item.get("sha256")
    if not isinstance(digest, str) or _sha256(path) != digest:
        raise ValueError(f"native evidence artifact digest mismatch: {relative}")


def native_orfs_run_view(workdir: str | Path, *, allow_non_success: bool = False) -> dict[str, Any]:
    """Validate one completed native ORFS directory and make a read-only view.

    The returned payload deliberately carries ``evidence_origin`` so callers
    cannot confuse it with a Runtime database view.  Metrics are accepted only
    when they agree with the independently generated analysis report.
    """
    root = Path(workdir).expanduser().resolve()
    plan = _read_json(root / "plan.json")
    result = _read_json(root / "run_result.json")
    if result.get("run_id") != plan.get("run_id") or result.get("design") != plan.get("design"):
        raise ValueError("native plan/result identity mismatch")
    if Path(str(result.get("workdir") or "")).expanduser().resolve() != root:
        raise ValueError("native result workdir mismatch")
    request = plan.get("request")
    if not isinstance(request, dict):
        raise ValueError("native plan has no request")
    for key in ("rtl_path", "platform", "target_stage", "clock_period_ns",
                "core_utilization_pct", "place_density"):
        if key not in request:
            raise ValueError(f"native request lacks {key}")
    rtl = Path(str(request["rtl_path"])).expanduser().resolve()
    if not rtl.is_file():
        raise ValueError("native RTL input is unavailable for fingerprinting")
    artifacts = _artifact_map(result)
    _verify_artifact(root, artifacts, "plan.json")
    _verify_artifact(root, artifacts, "analysis/report.json")
    report = _read_json(root / "analysis/report.json")
    if report.get("design") != plan.get("design") or report.get("platform") != request.get("platform"):
        raise ValueError("native analysis report does not match its plan")
    succeeded = result.get("status") == "succeeded" and report.get("flow_status") == "completed"
    if not succeeded and not allow_non_success:
        raise ValueError("native ORFS evidence is not a completed successful run")
    kpi = report.get("kpi")
    metric_rows = result.get("metrics")
    if not isinstance(kpi, dict) or not isinstance(metric_rows, list):
        raise ValueError("native ORFS evidence lacks report KPIs or result metrics")
    metrics = {row.get("name"): row.get("value") for row in metric_rows if isinstance(row, dict)}
    if succeeded:
        for metric_name, report_name in _REPORT_METRICS.items():
            result_value, report_value = metrics.get(metric_name), kpi.get(report_name)
            if (isinstance(result_value, bool) or not isinstance(result_value, (int, float))
                    or isinstance(report_value, bool) or not isinstance(report_value, (int, float))
                    or float(result_value) != float(report_value)):
                raise ValueError(f"native metric conflicts with analysis report: {metric_name}")
    tools = plan.get("tools")
    if not isinstance(tools, dict):
        raise ValueError("native plan has no toolchain snapshot")
    toolchain_profile = hashlib.sha256(json.dumps(tools, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    parameter_keys = ("platform", "target_stage", "clock_period_ns", "core_utilization_pct",
                      "place_density", "minimum_die_size_um", "stage_timeout_seconds")
    parameters = {key: request[key] for key in parameter_keys if request.get(key) is not None}
    return {
        "evidence_origin": "native-orfs-cli-artifacts",
        "execution_allowed": False,
        "run": {"status": str(result.get("status") or "failed"), "task_spec": {
            "task_id": str(plan["run_id"]), "design_id": str(plan["design"]), "plugin_id": "orfs",
            "inputs": {"rtl": {"sha256": _sha256(rtl)}, "top": request.get("top"),
                       "clock": request.get("clock")},
            "parameters": parameters,
            "resources": {"toolchain_profile": toolchain_profile},
            "labels": {"evidence_origin": "native-orfs-cli-artifacts"},
        }},
        "stages": [{"attempts": [{"metrics": metric_rows}]}],
        "evidence": {"plan_sha256": artifacts["plan.json"]["sha256"],
                     "analysis_report_sha256": artifacts["analysis/report.json"]["sha256"]},
    }
