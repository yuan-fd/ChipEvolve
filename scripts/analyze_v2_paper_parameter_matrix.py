#!/usr/bin/env python3
"""Apply the frozen paired statistics to the v2 BO/GP-versus-random matrix."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3
import statistics
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DESIGNS = ("gcd", "fifo", "uart_tx", "ibex_alu")
PROFILE_WEIGHTS = {
    "balanced": {"setup_wns_ns": ("max", .40), "area_um2": ("min", .35),
                 "power_W": ("min", .25)},
    "area": {"area_um2": ("min", 1.0)},
    "timing": {"setup_wns_ns": ("max", 1.0)},
    "performance": {"setup_wns_ns": ("max", 1.0)},
    "power": {"power_W": ("min", 1.0)},
}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("quantile requires observations")
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position); upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def _sign_flip_pvalue(values: list[float], *, seed: int, draws: int = 200_000) -> dict:
    """Two-sided paired randomization test using the absolute mean statistic."""
    if not values:
        raise ValueError("sign-flip test requires paired observations")
    observed = abs(statistics.fmean(values))
    n = len(values)
    if n <= 20:
        extreme = 0; total = 1 << n
        for mask in range(total):
            statistic = abs(sum(value if mask & (1 << index) else -value
                                for index, value in enumerate(values)) / n)
            extreme += statistic >= observed - 1e-15
        return {"method": "exact", "draws": total, "statistic": observed,
                "p_value": extreme / total}
    rng = random.Random(seed); extreme = 0
    for _ in range(draws):
        statistic = abs(sum(value if rng.getrandbits(1) else -value
                            for value in values) / n)
        extreme += statistic >= observed - 1e-15
    return {"method": "monte_carlo", "draws": draws, "statistic": observed,
            "p_value": (extreme + 1) / (draws + 1)}


def _bootstrap_median(values: list[float], *, seed: int, draws: int = 50_000) -> dict:
    rng = random.Random(seed); n = len(values)
    samples = [statistics.median([values[rng.randrange(n)] for _ in range(n)])
               for _ in range(draws)]
    return {"method": "seeded_nonparametric_bootstrap", "draws": draws,
            "estimate": statistics.median(values),
            "confidence_level": .95,
            "lower": _quantile(samples, .025), "upper": _quantile(samples, .975)}


def _holm(pvalues: dict[str, float]) -> dict[str, dict]:
    ordered = sorted(pvalues, key=pvalues.get); count = len(ordered); running = 0.0
    result: dict[str, dict] = {}
    for rank, name in enumerate(ordered, start=1):
        adjusted = min(1.0, (count - rank + 1) * pvalues[name])
        running = max(running, adjusted)
        result[name] = {"raw_p_value": pvalues[name], "holm_adjusted_p_value": running,
                        "rank": rank, "reject_at_0_05": running <= .05}
    return result


def _duration_hours(rows: Iterable[dict]) -> float:
    total = 0.0
    for row in rows:
        if not row.get("started_at") or not row.get("ended_at"):
            continue
        total += (datetime.fromisoformat(row["ended_at"])
                  - datetime.fromisoformat(row["started_at"])).total_seconds() / 3600
    return total


def _runtime_rows(database: Path, run_ids: list[str]) -> list[dict]:
    if not database.is_file():
        raise FileNotFoundError(database)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"SELECT run_id, status, started_at, ended_at FROM runtime_runs "
            f"WHERE run_id IN ({','.join('?' for _ in run_ids)})", run_ids).fetchall()
    found = {row["run_id"]: dict(row) for row in rows}
    missing = [run_id for run_id in run_ids if run_id not in found]
    if missing:
        raise ValueError(f"Runtime DB is missing registered random-arm runs: {missing}")
    return [found[run_id] for run_id in run_ids]


def _best_curve(items: list[dict], floor: float) -> tuple[list[float], dict]:
    best = floor; curve = []; selected: dict = {}
    for item in items:
        summary = item.get("summary") or {}
        utility = item.get("utility")
        if summary.get("eligible") is True and utility is not None and float(utility) >= best:
            best = float(utility); selected = item
        curve.append(best)
    return curve, selected


def _profile_utility(summary: dict, baseline: dict, profile: str) -> float | None:
    if summary.get("eligible") is not True or baseline.get("eligible") is not True:
        return None
    utility = 0.0
    for metric, (direction, weight) in PROFILE_WEIGHTS[profile].items():
        current = float(summary["metrics"][metric]["median"])
        reference = float(baseline["metrics"][metric]["median"])
        improvement = ((reference - current) / max(abs(reference), 1e-12)
                       if direction == "min" else
                       (current - reference) / max(abs(reference), 1e-12))
        utility += weight * improvement
    return utility


def _profile_replay(items: list[dict]) -> dict:
    baseline = items[0]["summary"]
    result = {}
    for profile in PROFILE_WEIGHTS:
        scored = [(index, _profile_utility(item["summary"], baseline, profile))
                  for index, item in enumerate(items)]
        eligible = [(index, value) for index, value in scored if value is not None]
        index, value = max(eligible, key=lambda pair: pair[1])
        result[profile] = {"selected_index": index, "utility": value,
                           "metrics": items[index]["summary"]["metrics"]}
    return result


def _bo_cell(path: Path, design: str, floor: float) -> dict:
    detail = _read(path / design / "report.json")
    history = detail["checkpoint"]["state"]["history"]
    curve, selected = _best_curve(history, floor)
    return {"best_utility": curve[-1], "curve": curve,
            "profile_replay": _profile_replay(history),
            "selected_round": selected.get("round"),
            "selected_summary": selected.get("summary"),
            "terminal_status": detail["checkpoint"]["state"]["status"],
            "failure_runs": sum(row["status"] != "succeeded" for row in detail["runtime_runs"]),
            "run_count": len(detail["runtime_runs"]),
            "summed_run_wall_hours": _duration_hours(detail["runtime_runs"])}


def _random_cell(path: Path, design: str, floor: float) -> dict:
    report = _read(path / "report.json")
    row = next(item for item in report["design_rows"] if item["design"] == design)
    items = [row["baseline"], *row["candidates"]]
    curve, selected = _best_curve(items, floor)
    run_ids = [run_id for item in items for run_id in item["run_ids"]]
    runtime_rows = _runtime_rows(path / "runtime.db", run_ids)
    statuses = [item["status"] for item in runtime_rows]
    return {"best_utility": curve[-1], "curve": curve,
            "profile_replay": _profile_replay(items),
            "selected_round": selected.get("vector_index"),
            "selected_summary": selected.get("summary"),
            "terminal_status": "completed" if all(x == "succeeded" for x in statuses) else "failed",
            "failure_runs": sum(status != "succeeded" for status in statuses),
            "run_count": len(runtime_rows),
            "summed_run_wall_hours": _duration_hours(runtime_rows)}


def analyze(matrix: Path, protocol_path: Path) -> dict:
    protocol_bytes = protocol_path.read_bytes()
    snapshot = matrix / "protocol.snapshot.json"
    if not snapshot.is_file() or snapshot.read_bytes() != protocol_bytes:
        raise ValueError("matrix protocol snapshot does not match the requested frozen protocol")
    protocol = json.loads(protocol_bytes); frozen = protocol["parameter_policy_primary"]
    floor = float(frozen["infeasible_cell_value"]); rows = []
    for seed in frozen["policy_seeds"]:
        for design in frozen["designs"]:
            bo = _bo_cell(matrix / "bo_gp" / f"seed-{seed}", design, floor)
            random_cell = _random_cell(matrix / "seeded_random" / f"seed-{seed}", design, floor)
            rows.append({"seed": seed, "design": design, "bo_gp": bo,
                         "seeded_random": random_cell,
                         "paired_difference": bo["best_utility"] - random_cell["best_utility"],
                         "winner": "bo_gp" if bo["best_utility"] > random_cell["best_utility"]
                         else "seeded_random" if random_cell["best_utility"] > bo["best_utility"]
                         else "tie"})
    differences = [row["paired_difference"] for row in rows]
    per_design_tests = {name: _sign_flip_pvalue(
        [row["paired_difference"] for row in rows if row["design"] == name],
        seed=20260825 + index) for index, name in enumerate(DESIGNS)}
    adjusted = _holm({name: test["p_value"] for name, test in per_design_tests.items()})
    for name in DESIGNS:
        per_design_tests[name].update(adjusted[name])
    return {
        "schema_version": 1, "kind": "v2_paper_parameter_frozen_analysis",
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": hashlib.sha256(protocol_bytes).hexdigest(), "status": "passed",
        "cell_count": len(rows), "run_count": sum(
            row[arm]["run_count"] for row in rows for arm in ("bo_gp", "seeded_random")),
        "primary": {
            "mean_paired_difference": statistics.fmean(differences),
            "median_paired_difference": statistics.median(differences),
            "sign_flip": _sign_flip_pvalue(differences, seed=20260825),
            "bootstrap_median_95_ci": _bootstrap_median(differences, seed=20260825),
            "wins": {name: sum(row["winner"] == name for row in rows)
                     for name in ("bo_gp", "seeded_random", "tie")},
        },
        "per_design_secondary": per_design_tests,
        "threshold_hit_rate": {arm: sum(row[arm]["best_utility"] >= .005 for row in rows) / len(rows)
                               for arm in ("bo_gp", "seeded_random")},
        "failure_runs": {arm: sum((row[arm]["failure_runs"] or 0) for row in rows)
                         for arm in ("bo_gp", "seeded_random")},
        "anytime_mean_best_utility": {arm: [statistics.fmean(row[arm]["curve"][index]
                                                   for row in rows) for index in range(4)]
                                      for arm in ("bo_gp", "seeded_random")},
        "objective_profile_replay": {
            "scope": "post-hoc selection over the same four measured vectors; proves the preference changes ranking, not that every profile followed its own BO trajectory",
            "timing_performance_alias": True,
            "selection_difference_from_balanced": {
                arm: {profile: sum(
                    row[arm]["profile_replay"][profile]["selected_index"]
                    != row[arm]["profile_replay"]["balanced"]["selected_index"]
                    for row in rows)
                    for profile in ("area", "timing", "performance", "power")}
                for arm in ("bo_gp", "seeded_random")
            },
        },
        "cells": rows,
        "claim_boundary": "Frozen paired analysis over four fixed designs and ten policy seeds; it does not establish universal superiority across arbitrary RTL or PDKs.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--protocol", type=Path,
                        default=ROOT / "experiments/v2-paper-20260825/protocol.json")
    args = parser.parse_args()
    result = analyze(args.matrix.expanduser().resolve(), args.protocol.expanduser().resolve())
    output = args.output.expanduser().resolve(); output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    temporary.replace(output)
    print(json.dumps({"output": str(output), "status": result["status"],
                      "cells": result["cell_count"], "runs": result["run_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
