#!/usr/bin/env python3
"""Aggregate three-seed BO/GP versus random evidence without overclaiming."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics


DESIGNS = ("gcd", "fifo", "uart_tx", "ibex_alu")


def _summary(values: list[float]) -> dict:
    return {"count": len(values), "median": statistics.median(values),
            "minimum": min(values), "maximum": max(values), "values": values}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bo-seed1", type=Path, required=True)
    parser.add_argument("--bo-suite", type=Path, action="append", required=True)
    parser.add_argument("--random", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    first = json.loads(args.bo_seed1.expanduser().resolve().read_text(encoding="utf-8"))
    bo: dict[str, list[dict]] = {name: [] for name in DESIGNS}
    for row in first["design_rows"]:
        bo[row["design"]].append({"seed": 20260824,
                                  "best_utility": row["best_utility"],
                                  "met_threshold": row["met_practical_threshold"],
                                  "runs": row["runs"]})
    for path in args.bo_suite:
        suite = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
        for row in suite["design_rows"]:
            bo[row["design"]].append({"seed": suite["seed"],
                                      "best_utility": row["best_utility"],
                                      "met_threshold": row["met_practical_threshold"],
                                      "runs": row["runs"]})
    random: dict[str, list[dict]] = {name: [] for name in DESIGNS}
    random_runs = 0; random_failures = 0
    for path in args.random:
        report = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
        random_runs += int(report["run_count"])
        random_failures += sum(status != "succeeded" for status in report["terminal_statuses"])
        for row in report["design_rows"]:
            random[row["design"]].append({"seed": report["seed"],
                                          "best_utility": row["best_utility"],
                                          "met_threshold": row["met_practical_threshold"],
                                          "runs": 12})
    rows = []
    for design in DESIGNS:
        bo_values = [float(item["best_utility"]) for item in bo[design]]
        random_values = [float(item["best_utility"]) for item in random[design]]
        bo_summary, random_summary = _summary(bo_values), _summary(random_values)
        rows.append({
            "design": design, "bo_seeds": bo[design], "random_seeds": random[design],
            "bo_best_utility": bo_summary, "random_best_utility": random_summary,
            "bo_threshold_seed_rate": sum(x["met_threshold"] for x in bo[design]) / 3,
            "random_threshold_seed_rate": sum(x["met_threshold"] for x in random[design]) / 3,
            "median_winner": "bo" if bo_summary["median"] > random_summary["median"]
                else "random" if random_summary["median"] > bo_summary["median"] else "tie",
        })
    bo_runs = sum(item["runs"] for values in bo.values() for item in values)
    checks = {
        "three_seeds_per_policy_design": all(
            len(bo[name]) == len(random[name]) == 3 for name in DESIGNS),
        "equal_total_run_budget": bo_runs == random_runs == 144,
        "all_random_runs_succeeded": random_failures == 0,
        "all_bo_seed_suites_accepted": all(
            item["runs"] == 12 for values in bo.values() for item in values),
    }
    result = {
        "schema_version": 1, "kind": "v2_multiseed_parameter_policy_ablation",
        "status": "passed" if all(checks.values()) else "failed", "checks": checks,
        "bo_run_count": bo_runs, "random_run_count": random_runs,
        "bo_threshold_events": sum(item["met_threshold"] for values in bo.values() for item in values),
        "random_threshold_events": sum(item["met_threshold"] for values in random.values() for item in values),
        "bo_threshold_event_rate": sum(item["met_threshold"] for values in bo.values() for item in values) / 12,
        "random_threshold_event_rate": sum(item["met_threshold"] for values in random.values() for item in values) / 12,
        "median_design_wins": {
            "bo": sum(row["median_winner"] == "bo" for row in rows),
            "random": sum(row["median_winner"] == "random" for row in rows),
            "tie": sum(row["median_winner"] == "tie" for row in rows),
        },
        "design_rows": rows,
        "claim_boundary": (
            "Across three fixed seeds and four designs, the report is descriptive. "
            "Twelve design-seed cells are insufficient for a universal superiority or "
            "statistical-significance claim; all raw terminal runs remain included."),
    }
    destination = args.output.expanduser().resolve(); destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2),
                           encoding="utf-8")
    print(json.dumps({"output": str(destination), "status": result["status"],
                      "bo_threshold_events": result["bo_threshold_events"],
                      "random_threshold_events": result["random_threshold_events"],
                      "median_design_wins": result["median_design_wins"]}, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
