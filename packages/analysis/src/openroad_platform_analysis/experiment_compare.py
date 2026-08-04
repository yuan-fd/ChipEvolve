"""Index and compare versioned gray-box experiments."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def _read(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _stable_id(manifest: dict, run_dir: Path) -> str:
    return manifest.get("experiment_id") or (
        f"{manifest.get('run_id', run_dir.name)}-{hashlib.sha256(str(run_dir.resolve()).encode()).hexdigest()[:8]}"
    )


def index_experiments(project_root: str | Path) -> list[dict]:
    root = Path(project_root).resolve()
    manifests = []
    for base in (root / "demo_output/gds", root / "demo_output/experiments"):
        if not base.is_dir():
            continue
        for path in base.rglob("analysis/run_manifest.json"):
            manifest = _read(path, {})
            if not isinstance(manifest, dict) or not manifest.get("design"):
                continue
            run_dir = path.parent.parent
            evaluation = _read(path.parent / "evaluation.json", {})
            manifests.append({
                "experiment_id": _stable_id(manifest, run_dir),
                "run_id": manifest.get("run_id", run_dir.name),
                "created_at": manifest.get("created_at"),
                "design": manifest.get("design"), "platform": manifest.get("platform"),
                "status": manifest.get("status"), "target_stage": manifest.get("target_stage"),
                "rtl_sha256": (manifest.get("inputs") or {}).get("rtl_sha256"),
                "classification": evaluation.get("classification", "legacy_unclassified"),
                "workdir": str(run_dir.relative_to(root)),
                "manifest_path": str(path.relative_to(root)),
            })
    manifests.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return manifests


def load_experiment(project_root: str | Path, experiment_id: str) -> dict | None:
    root = Path(project_root).resolve()
    entry = next((item for item in index_experiments(root)
                  if item["experiment_id"] == experiment_id), None)
    if not entry:
        return None
    run_dir = (root / entry["workdir"]).resolve()
    try:
        run_dir.relative_to(root / "demo_output")
    except ValueError:
        return None
    analysis = run_dir / "analysis"
    return {
        "index": entry,
        "manifest": _read(analysis / "run_manifest.json", {}),
        "metrics": _read(analysis / "stage_metrics.json", {}),
        "deltas": _read(analysis / "stage_deltas.json", {}),
        "graybox": _read(analysis / "graybox.json", {}),
        "parameters": _read(analysis / "parameter_provenance.json", {}),
        "artifacts": _read(analysis / "artifact_registry.json", {}),
        "events": _read(analysis / "log_events.json", {}),
        "evaluation": _read(analysis / "evaluation.json", {}),
    }


def _get(mapping: dict, dotted: str):
    value = mapping
    for part in dotted.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _compatibility(experiments: list[dict]) -> dict:
    checks = (
        ("design", "design", True),
        ("rtl_sha256", "inputs.rtl_sha256", True),
        ("platform", "platform", True),
        ("clock_period_ns", "requested_parameters.clock_period_ns", True),
        ("core_utilization_pct", "requested_parameters.core_utilization_pct", True),
        ("strategy", "strategy", True),
        ("target_stage", "target_stage", True),
        ("openroad_version", "tools.openroad", True),
        ("yosys_version", "tools.yosys", True),
        ("orfs_commit", "tools.orfs_commit", True),
        ("platform_config_sha256", "platform_provenance.orfs_platform_config_sha256", True),
        ("random_seed", "random_seed.value", False),
    )
    rows = []
    for name, path, required in checks:
        values = [_get(experiment["manifest"], path) for experiment in experiments]
        missing = any(value is None for value in values)
        same = len({json.dumps(value, sort_keys=True) for value in values}) <= 1
        status = "unknown" if missing else ("pass" if same else "fail")
        rows.append({"check": name, "required": required, "status": status, "values": values})
    flow_values = [experiment["manifest"].get("status") for experiment in experiments]
    flow_complete = all(value == "completed" for value in flow_values)
    rows.append({"check": "flow_complete", "required": True,
                 "status": "pass" if flow_complete else "fail", "values": flow_values})
    comparable = all(row["status"] == "pass" for row in rows if row["required"])
    return {"comparable": comparable, "checks": rows,
            "warning": None if comparable else "关键输入或工具环境不同，只能查看差异，不能作强因果归因。"}


def _parameter_diff(experiments: list[dict]) -> list[dict]:
    maps = [{**(item["manifest"].get("requested_parameters") or {}),
             "strategy": item["manifest"].get("strategy")} for item in experiments]
    keys = sorted(set().union(*(mapping.keys() for mapping in maps)))
    return [{"parameter": key, "values": [mapping.get(key) for mapping in maps],
             "changed": len({json.dumps(mapping.get(key), sort_keys=True) for mapping in maps}) > 1}
            for key in keys]


def _metric_diff(experiments: list[dict]) -> list[dict]:
    stages = ("synth", "floorplan", "place", "cts", "route", "finish")
    rows = []
    for stage in stages:
        metric_maps = [((item["metrics"].get("stages") or {}).get(stage, {}).get("metrics") or {})
                       for item in experiments]
        keys = sorted(set().union(*(mapping.keys() for mapping in metric_maps)))
        for key in keys:
            values = [mapping.get(key) for mapping in metric_maps]
            base = values[0]
            deltas = [None if not isinstance(value, (int, float)) or not isinstance(base, (int, float))
                      else round(value - base, 6) for value in values]
            rows.append({"stage": stage, "metric": key, "values": values,
                         "delta_from_baseline": deltas})
    return rows


def _event_diff(experiments: list[dict]) -> dict:
    sets = []
    for item in experiments:
        events = item["events"].get("events", []) if isinstance(item["events"], dict) else []
        sets.append({(str(event.get("severity") or ""), str(event.get("category") or ""),
                      str(event.get("message") or "")) for event in events})
    baseline = sets[0] if sets else set()
    return {"per_experiment": [{
        "new": [list(value) for value in sorted(values - baseline)],
        "resolved": [list(value) for value in sorted(baseline - values)],
    } for values in sets]}


def _artifact_diff(experiments: list[dict]) -> list[dict]:
    summaries = []
    for item in experiments:
        artifacts = item["artifacts"].get("artifacts", []) if isinstance(item["artifacts"], dict) else []
        by_type = {}
        for artifact in artifacts:
            by_type[artifact.get("artifact_type", "unknown")] = by_type.get(artifact.get("artifact_type", "unknown"), 0) + 1
        summaries.append({"count": len(artifacts), "by_type": by_type})
    return summaries


def _sensitivity_series(experiments: list[dict]) -> dict:
    """Build evidence-backed response curves for the one changed numeric input."""
    parameter_rows = [row for row in _parameter_diff(experiments) if row["changed"]]
    numeric = [row for row in parameter_rows
               if all(isinstance(value, (int, float)) and not isinstance(value, bool)
                      for value in row["values"])]
    if len(numeric) != 1:
        return {"available": False, "reason": "需要且只能有一个变化的数值参数。",
                "parameter": None, "series": []}
    parameter = numeric[0]
    preferred = {
        "utilization_pct", "estimated_wirelength_um", "wirelength_um",
        "setup_wns_ns", "setup_tns_ns", "drc_errors", "congestion_overflow",
        "runtime_seconds", "instance_count", "area_um2",
    }
    series = []
    for row in _metric_diff(experiments):
        values = row["values"]
        if row["metric"] not in preferred or not all(
                value is None or isinstance(value, (int, float)) for value in values):
            continue
        points = [{"x": x, "y": y, "experiment_id": experiments[index]["index"]["experiment_id"]}
                  for index, (x, y) in enumerate(zip(parameter["values"], values)) if y is not None]
        if len(points) < 2:
            continue
        slopes = []
        ordered = sorted(points, key=lambda point: point["x"])
        for left, right in zip(ordered, ordered[1:]):
            dx = right["x"] - left["x"]
            slopes.append(None if dx == 0 else (right["y"] - left["y"]) / dx)
        series.append({"stage": row["stage"], "metric": row["metric"],
                       "points": ordered, "local_slopes": slopes})
    return {"available": bool(series),
            "reason": None if series else "没有至少两个可用的共同数值指标点。",
            "parameter": parameter["parameter"], "series": series}


def _influence_graph(experiments: list[dict]) -> dict:
    """Return an engineering hypothesis graph, clearly separated from observations."""
    changed = [row["parameter"] for row in _parameter_diff(experiments) if row["changed"]]
    templates = {
        "place_density": [
            ("place_density", "requested PLACE_DENSITY", "observed"),
            ("placement", "global placement packing", "hypothesis"),
            ("congestion", "routing congestion", "hypothesis"),
            ("wirelength", "route wirelength", "observed"),
            ("timing_drc", "timing / DRC outcome", "observed"),
        ],
        "core_utilization_pct": [
            ("core_utilization", "requested core utilization", "observed"),
            ("core_area", "effective core area", "observed"),
            ("placement", "cell packing pressure", "hypothesis"),
            ("congestion", "routing congestion", "hypothesis"),
            ("timing_drc", "timing / DRC outcome", "observed"),
        ],
        "clock_period_ns": [
            ("clock_period", "requested clock period", "observed"),
            ("optimization", "timing optimization pressure", "hypothesis"),
            ("buffers", "buffering / sizing", "hypothesis"),
            ("placement", "placement and routing demand", "hypothesis"),
            ("timing_drc", "timing / DRC outcome", "observed"),
        ],
    }
    parameter = changed[0] if len(changed) == 1 else None
    nodes = templates.get(parameter, [])
    return {
        "available": bool(nodes), "parameter": parameter,
        "nodes": [{"id": node_id, "label": label, "claim_type": claim_type}
                  for node_id, label, claim_type in nodes],
        "edges": [{"source": nodes[index][0], "target": nodes[index + 1][0],
                   "claim_type": "hypothesis"}
                  for index in range(max(0, len(nodes) - 1))],
        "notice": "边表示待验证的工程因果假设；observed 节点值来自本次实验指标。",
    }


def _checkpoint_diff(experiments: list[dict]) -> dict:
    """Compare stage checkpoints without claiming unavailable object-level movement."""
    stages = ("synth", "floorplan", "place", "cts", "route", "finish")
    rows = []
    for stage in stages:
        per_experiment = []
        for item in experiments:
            artifacts = item["artifacts"].get("artifacts", []) if isinstance(item["artifacts"], dict) else []
            stage_artifacts = [artifact for artifact in artifacts if artifact.get("stage") == stage]
            metrics = (((item["metrics"].get("stages") or {}).get(stage) or {}).get("metrics") or {})
            hashes = sorted({artifact.get("sha256") for artifact in stage_artifacts if artifact.get("sha256")})
            per_experiment.append({
                "experiment_id": item["index"]["experiment_id"],
                "artifact_count": len(stage_artifacts), "artifact_hashes": hashes,
                "instance_count": metrics.get("instance_count"),
                "area_um2": metrics.get("area_um2"),
                "utilization_pct": metrics.get("utilization_pct"),
                "wirelength_um": metrics.get("wirelength_um", metrics.get("estimated_wirelength_um")),
            })
        rows.append({"stage": stage, "experiments": per_experiment,
                     "checkpoint_changed": len({json.dumps(row["artifact_hashes"]) for row in per_experiment}) > 1})
    return {"stages": rows, "object_level_available": False,
            "notice": "当前比较检查点哈希、产物数量和阶段指标；未提取实例坐标时不推断单元移动。"}


def _candidate_outcomes(experiments: list[dict], comparable: bool) -> list[dict]:
    outcomes = []
    baseline = experiments[0]
    base_place = ((baseline["metrics"].get("stages") or {}).get("place", {}).get("metrics") or {})
    base_route = ((baseline["metrics"].get("stages") or {}).get("route", {}).get("metrics") or {})
    for index, item in enumerate(experiments):
        classification = item["evaluation"].get("classification", "insufficient_evidence")
        reason = "Hard-gate evaluation result."
        if index and comparable and classification == "valid":
            place = ((item["metrics"].get("stages") or {}).get("place", {}).get("metrics") or {})
            route = ((item["metrics"].get("stages") or {}).get("route", {}).get("metrics") or {})
            place_improved = (
                isinstance(place.get("estimated_wirelength_um"), (int, float)) and
                isinstance(base_place.get("estimated_wirelength_um"), (int, float)) and
                place["estimated_wirelength_um"] < base_place["estimated_wirelength_um"]
            )
            route_degraded = (
                isinstance(route.get("wirelength_um"), (int, float)) and
                isinstance(base_route.get("wirelength_um"), (int, float)) and
                route["wirelength_um"] > base_route["wirelength_um"]
            ) or (
                isinstance(route.get("setup_wns_ns"), (int, float)) and
                isinstance(base_route.get("setup_wns_ns"), (int, float)) and
                route["setup_wns_ns"] < base_route["setup_wns_ns"]
            )
            if place_improved and route_degraded:
                classification = "locally_improved_finally_degraded"
                reason = "Place wirelength improved, but a route metric degraded versus baseline."
        outcomes.append({"experiment_id": item["index"]["experiment_id"],
                         "classification": classification, "reason": reason})
    return outcomes


def compare_experiments(project_root: str | Path, experiment_ids: list[str]) -> dict:
    if len(experiment_ids) < 2:
        raise ValueError("至少选择两个实验")
    experiments = [load_experiment(project_root, experiment_id) for experiment_id in experiment_ids]
    if any(item is None for item in experiments):
        raise ValueError("包含不存在的实验")
    compatibility = _compatibility(experiments)
    return {
        "schema_version": 3,
        "experiment_ids": experiment_ids,
        "baseline_experiment_id": experiment_ids[0],
        "compatibility": compatibility,
        "parameter_differences": _parameter_diff(experiments),
        "metric_differences": _metric_diff(experiments),
        "event_differences": _event_diff(experiments),
        "artifact_summaries": _artifact_diff(experiments),
        "sensitivity": _sensitivity_series(experiments),
        "influence_graph": _influence_graph(experiments),
        "checkpoint_differences": _checkpoint_diff(experiments),
        "candidate_outcomes": _candidate_outcomes(experiments, compatibility["comparable"]),
    }
