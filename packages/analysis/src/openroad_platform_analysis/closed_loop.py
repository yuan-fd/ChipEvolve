"""Statistics and evidence decisions for the v2 BO/GP closed loop.

This module is intentionally data-only.  It neither submits EDA work nor asks
an LLM to judge its own proposal.  Runtime observations enter here only after
the execution layer has produced immutable evidence.
"""
from __future__ import annotations

import hashlib
import math
import statistics
from typing import Any, Iterable, Mapping, Sequence

from openroad_platform_contracts import LearningObservation, ObjectiveSpec


def paired_replica_seeds(experiment_seed: int, count: int) -> tuple[int, ...]:
    """Derive stable OpenROAD seeds shared by every compared policy arm."""
    if (not isinstance(experiment_seed, int) or isinstance(experiment_seed, bool)
            or not 0 <= experiment_seed <= 2_147_483_647):
        raise ValueError("experiment_seed must be between 0 and 2147483647")
    if not 2 <= count <= 8:
        raise ValueError("paired replica count must be between 2 and 8")
    result: list[int] = []
    index = 0
    while len(result) < count:
        digest = hashlib.sha256(
            f"openroad-v2-paired-or-seed:{experiment_seed}:{index}".encode()
        ).digest()
        value = int.from_bytes(digest[:4], "big") & 0x7fffffff
        if value not in result:
            result.append(value)
        index += 1
    return tuple(result)


def _quantile(values: Sequence[float], fraction: float) -> float:
    if len(values) == 1:
        return float(values[0])
    position = (len(values) - 1) * fraction
    low = math.floor(position); high = math.ceil(position)
    if low == high:
        return float(values[low])
    return float(values[low] * (high - position) + values[high] * (position - low))


def summarize_replicates(observations: Iterable[LearningObservation],
                         objectives: Sequence[ObjectiveSpec],
                         hard_constraints: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    items = tuple(observations)
    if not items:
        raise ValueError("at least one replica observation is required")
    successful = [item for item in items if item.status == "succeeded"]
    metrics: dict[str, dict[str, float | int]] = {}
    for objective in objectives:
        values = sorted(float(item.metrics[objective.metric_name]) for item in successful
                        if objective.metric_name in item.metrics)
        if values:
            metrics[objective.metric_name] = {
                "count": len(values), "median": float(statistics.median(values)),
                "minimum": values[0], "maximum": values[-1],
                "q1": _quantile(values, .25), "q3": _quantile(values, .75),
                "iqr": _quantile(values, .75) - _quantile(values, .25),
            }
    constraint_results = []
    for rule in hard_constraints:
        name, operator, threshold = (str(rule.get("metric") or ""),
                                     str(rule.get("operator") or ""), rule.get("threshold"))
        values = [item.metrics.get(name) for item in successful]
        complete = len(values) == len(items) and all(value is not None for value in values)
        if operator == "<=":
            passed = complete and all(float(value) <= float(threshold) for value in values)
        elif operator == ">=":
            passed = complete and all(float(value) >= float(threshold) for value in values)
        elif operator == "==":
            passed = complete and all(value == threshold for value in values)
        else:
            raise ValueError("hard constraint operator must be <=, >=, or ==")
        constraint_results.append({"metric": name, "operator": operator,
                                   "threshold": threshold, "values": values,
                                   "passed": bool(passed)})
    complete_objectives = all(
        metrics.get(objective.metric_name, {}).get("count") == len(items)
        for objective in objectives
    )
    return {
        "replicas": len(items), "successes": len(successful),
        "failure_rate": (len(items) - len(successful)) / len(items),
        "metrics": metrics, "constraints": constraint_results,
        "complete_objectives": complete_objectives,
        "eligible": len(successful) == len(items) and complete_objectives
                    and all(item["passed"] for item in constraint_results),
        "run_ids": [item.run_id for item in items],
        "claim_boundary": "replicated observed Runtime evidence; no significance claim",
    }


def relative_utility(summary: Mapping[str, Any], reference: Mapping[str, Any],
                     objectives: Sequence[ObjectiveSpec]) -> float | None:
    """Weighted relative improvement; positive means better than reference."""
    if (not summary.get("eligible") or not reference.get("complete_objectives")
            or reference.get("successes") != reference.get("replicas")):
        return None
    total_weight = sum(float(item.weight) for item in objectives)
    utility = 0.0
    for objective in objectives:
        current = float(summary["metrics"][objective.metric_name]["median"])
        baseline = float(reference["metrics"][objective.metric_name]["median"])
        scale = max(abs(baseline), 1e-12)
        improvement = ((baseline - current) / scale if objective.direction == "min"
                       else (current - baseline) / scale)
        utility += float(objective.weight) / total_weight * improvement
    return float(utility)


def stalled_decision(*, candidate_utility: float | None, best_utility: float,
                     minimum_relative_improvement: float, stalled_rounds: int) -> dict[str, Any]:
    if minimum_relative_improvement < 0:
        raise ValueError("minimum_relative_improvement must be nonnegative")
    improvement = None if candidate_utility is None else candidate_utility - best_utility
    promoted = improvement is not None and improvement >= minimum_relative_improvement
    return {
        "promoted": promoted, "candidate_utility": candidate_utility,
        "previous_best_utility": best_utility, "incremental_improvement": improvement,
        "minimum_relative_improvement": minimum_relative_improvement,
        "stalled_rounds": 0 if promoted else stalled_rounds + 1,
        "reason": ("pre-registered improvement threshold met" if promoted
                   else "ineligible or below pre-registered improvement threshold"),
    }


def diagnosis_packet(history: Sequence[Mapping[str, Any]],
                     objectives: Sequence[ObjectiveSpec]) -> dict[str, Any]:
    failures = sum(int(item.get("summary", {}).get("failure_rate", 0) > 0)
                   for item in history)
    constrained = [rule for item in history for rule in item.get("summary", {}).get("constraints", [])
                   if not rule.get("passed")]
    last = history[-1] if history else {}
    return {
        "kind": "bounded_parameter_stall_diagnosis",
        "failed_rounds": failures, "violated_constraints": constrained[-12:],
        "last_round": last.get("round"),
        "objective_metrics": [item.metric_name for item in objectives],
        "next": "repair_agent_stage_localization",
        "execution_allowed": False,
        "note": "diagnosis selects a stage/evidence request; v3 repair tools are not executed",
    }
