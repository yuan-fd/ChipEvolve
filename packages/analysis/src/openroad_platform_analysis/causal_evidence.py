"""Fail-closed two-factor and cross-design intervention evidence.

A 2x2 experiment says something only about one design. This module keeps the
fixed context explicit and treats another design as validation or rejection,
never as permission to run an action automatically.
"""
from __future__ import annotations

import hashlib
import json
from itertools import product
from typing import Iterable, Mapping


def _fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _contexts(task: Mapping, first: str, second: str) -> tuple[str, str, str | None]:
    """Return local context, transfer context and immutable RTL fingerprint."""
    local = dict(task)
    local.pop("task_id", None)
    parameters = dict(local.get("parameters") or {})
    local["parameters"] = {key: value for key, value in parameters.items()
                           if key not in {first, second}}
    labels = dict(local.get("labels") or {})
    for key in ("replica_index", "evolution_campaign_id", "evolution_phase"):
        labels.pop(key, None)
    local["labels"] = labels
    rtl = (dict(local.get("inputs") or {}).get("rtl") or {})
    rtl_sha = rtl.get("sha256") if isinstance(rtl, Mapping) else None

    # For transfer matching, only design identity and RTL bytes are removed.
    # Platform, target stage, pinned toolchain profile and all other knobs stay.
    transfer = dict(local)
    transfer.pop("design_id", None)
    inputs = dict(transfer.get("inputs") or {})
    # Top-module names and clock port names belong to the held-out design;
    # keeping either would make every legitimate cross-design replication
    # ineligible. PDK/stage/period/toolchain and all non-design knobs remain.
    for key in ("rtl", "top", "clock"):
        inputs.pop(key, None)
    transfer["inputs"] = inputs
    return _fingerprint(local), _fingerprint(transfer), str(rtl_sha) if isinstance(rtl_sha, str) else None


def factorial_interaction_report(run_views: Iterable[dict], *, first: str, second: str, metric: str) -> dict:
    """Estimate effects only for a balanced repeated 2x2 intervention."""
    views = list(run_views)
    if len(views) < 8:
        return {"causal_eligible": False, "reason": "need two repetitions at each 2x2 corner"}
    rows, local_contexts, transfer_contexts, rtl_fingerprints = [], set(), set(), set()
    for view in views:
        run = view.get("run", {}); task = run.get("task_spec", {}); parameters = task.get("parameters", {})
        values = [item.get("value") for stage in view.get("stages", []) for attempt in stage.get("attempts", [])
                  for item in attempt.get("metrics", []) if item.get("name") == metric and isinstance(item.get("value"), (int, float))]
        if run.get("status") != "succeeded" or not values or first not in parameters or second not in parameters:
            return {"causal_eligible": False, "reason": "each run needs both parameters and a successful metric"}
        local, transfer, rtl_sha = _contexts(task, first, second)
        local_contexts.add(local); transfer_contexts.add(transfer)
        if rtl_sha: rtl_fingerprints.add(rtl_sha)
        rows.append((float(parameters[first]), float(parameters[second]), float(values[-1])))
    if len(local_contexts) != 1:
        return {"causal_eligible": False, "reason": "non-intervention context differs"}
    if len(rtl_fingerprints) != 1:
        return {"causal_eligible": False, "reason": "experiment mixes RTL design fingerprints"}
    if len(transfer_contexts) != 1:
        return {"causal_eligible": False, "reason": "non-design transfer context differs"}
    xs, ys = sorted({x for x, _, _ in rows}), sorted({y for _, y, _ in rows})
    if len(xs) != 2 or len(ys) != 2:
        return {"causal_eligible": False, "reason": "requires exactly two levels per parameter"}
    means, counts = {}, {}
    for x, y in product(xs, ys):
        sample = [z for a, b, z in rows if a == x and b == y]
        if len(sample) < 2:
            return {"causal_eligible": False, "reason": "each corner needs two repetitions"}
        key = f"{x:g}|{y:g}"; means[key] = sum(sample) / len(sample); counts[key] = len(sample)
    m00, m01, m10, m11 = (means[f"{x:g}|{y:g}"] for x, y in product(xs, ys))
    return {"causal_eligible": True, "method": "balanced repeated 2x2 intervention contrast", "metric": metric,
            "levels": {first: xs, second: ys}, "corner_means": means, "corner_counts": counts,
            "first_main_effect": ((m10 + m11) - (m00 + m01)) / 2,
            "second_main_effect": ((m01 + m11) - (m00 + m10)) / 2,
            "interaction_effect": m11 - m10 - m01 + m00,
            "design_fingerprint": next(iter(rtl_fingerprints)),
            "transfer_context_fingerprint": next(iter(transfer_contexts)),
            "claim": "controlled local effect estimate; not transferable beyond recorded context"}


def validate_holdout_interaction(source: Mapping, holdout: Mapping, *, first: str, second: str, metric: str) -> dict:
    """Judge a pre-registered holdout; return evidence, never an action grant."""
    for name, report in (("source", source), ("holdout", holdout)):
        if not report.get("causal_eligible"):
            return {"eligible": False, "reason": f"{name} lacks balanced repeated intervention evidence", "execution_allowed": False}
        if report.get("metric") != metric:
            return {"eligible": False, "reason": f"{name} metric differs", "execution_allowed": False}
        levels = report.get("levels", {})
        if levels.get(first) != source.get("levels", {}).get(first) or levels.get(second) != source.get("levels", {}).get(second):
            return {"eligible": False, "reason": "holdout intervention levels differ", "execution_allowed": False}
    if source.get("design_fingerprint") == holdout.get("design_fingerprint"):
        return {"eligible": False, "reason": "holdout must use a new RTL fingerprint", "execution_allowed": False}
    if source.get("transfer_context_fingerprint") != holdout.get("transfer_context_fingerprint"):
        return {"eligible": False, "reason": "PDK/toolchain/stage/non-intervention context differs", "execution_allowed": False}
    source_effect, holdout_effect = float(source["interaction_effect"]), float(holdout["interaction_effect"])
    same_direction = source_effect == 0 == holdout_effect or source_effect * holdout_effect > 0
    return {"eligible": True, "outcome": "validated" if same_direction else "rejected",
            "source_interaction": source_effect, "holdout_interaction": holdout_effect,
            "claim": ("same-direction interaction reproduced on one controlled holdout; scope remains these two designs"
                      if same_direction else "source interaction did not reproduce on the controlled holdout"),
            "execution_allowed": False}
