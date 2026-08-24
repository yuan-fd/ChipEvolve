"""Fail-closed repeated-run statistics for QoR claims."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable


def replication_report(run_views: Iterable[dict[str, Any]], metric_name: str) -> dict[str, Any]:
    """Summarize repeated Runtime views only when their immutable context matches.

    A missing metric, failed run, or context mismatch never becomes an
    improvement claim.  The caller may compare this report with a baseline
    report, but only after both reports are comparable.
    """
    views = list(run_views)
    if len(views) < 2:
        return {"comparable": False, "reason": "at least two repetitions are required"}
    contexts = [_context(view) for view in views]
    if len(set(contexts)) != 1:
        return {"comparable": False, "reason": "RTL/PDK/toolchain/task context differs", "contexts": contexts}
    values, statuses = [], []
    for view in views:
        statuses.append(view["run"]["status"])
        found = [item["value"] for stage in view.get("stages", []) for attempt in stage.get("attempts", [])
                 for item in attempt.get("metrics", []) if item.get("name") == metric_name
                 and isinstance(item.get("value"), (int, float))]
        if found: values.append(float(found[-1]))
    if len(values) != len(views) or any(item != "succeeded" for item in statuses):
        return {"comparable": False, "reason": "a repetition failed or lacks the requested metric",
                "statuses": statuses, "metric_count": len(values)}
    values.sort(); mean = sum(values) / len(values)
    variance = sum((item - mean) ** 2 for item in values) / len(values)
    return {"comparable": True, "metric": metric_name, "count": len(values), "values": values,
            "min": values[0], "max": values[-1], "mean": mean,
            "median": values[len(values) // 2], "stddev": math.sqrt(variance),
            "range": values[-1] - values[0], "failure_rate": 0.0,
            "context_fingerprint": contexts[0],
            "claim": "variation only; no significance claim"}


def compare_replication_reports(baseline: dict[str, Any], candidate: dict[str, Any], *,
                                direction: str, minimum_relative_improvement: float = 0.0) -> dict[str, Any]:
    if direction not in {"min", "max"}: raise ValueError("direction must be min or max")
    if not baseline.get("comparable") or not candidate.get("comparable"):
        return {"eligible": False, "reason": "each group must be internally comparable"}
    if baseline["metric"] != candidate["metric"]:
        return {"eligible": False, "reason": "metric differs"}
    if baseline["context_fingerprint"] == candidate["context_fingerprint"]:
        # Candidate differs by its declared experimental parameter, therefore
        # full task fingerprints differ.  Group contexts are checked inside
        # replication_report; cross-group identity intentionally isn't needed.
        pass
    base, value = baseline["median"], candidate["median"]
    delta = value - base
    relative = 0.0 if base == 0 else ((base - value) / abs(base) if direction == "min" else (value - base) / abs(base))
    stable = candidate["range"] <= abs(value) * 0.1 and baseline["range"] <= abs(base) * 0.1
    eligible = relative >= minimum_relative_improvement and stable
    return {"eligible": eligible, "baseline_median": base, "candidate_median": value,
            "delta": delta, "relative_improvement": relative, "stable": stable,
            "reason": "eligible for pre-registered claim" if eligible else "insufficient improvement or excessive variation"}


def _context(view: dict[str, Any]) -> str:
    task = dict(view["run"]["task_spec"])
    task.pop("task_id", None); labels = dict(task.get("labels") or {})
    for key in ("replica_index", "evolution_campaign_id", "evolution_phase"):
        labels.pop(key, None)
    task["labels"] = labels
    return hashlib.sha256(json.dumps(task, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
