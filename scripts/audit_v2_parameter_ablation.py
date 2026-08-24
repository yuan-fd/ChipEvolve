#!/usr/bin/env python3
"""Audit equal-budget BO/GP versus seeded random across the fixed suite."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app import ApiState  # noqa: E402


def _same(left: float, right: float) -> bool:
    return math.isclose(float(left), float(right), rel_tol=0, abs_tol=1e-12)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bo-aggregate", type=Path, required=True)
    parser.add_argument("--random-experiment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--orfs-root", type=Path,
                        default=Path("/share/home/yuanwenjie/OpenROAD-flow-scripts"))
    args = parser.parse_args()
    bo = json.loads(args.bo_aggregate.expanduser().resolve().read_text(encoding="utf-8"))
    root = args.random_experiment.expanduser().resolve()
    random_report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    frozen = json.loads((root / "frozen-plan.json").read_text(encoding="utf-8"))
    state = ApiState(
        root / "platform.db", root / "uploads", args.orfs_root,
        design_root=root / "designs", legacy_root=root / "legacy",
        runtime_db_path=root / "runtime.db",
        optimization_db_path=root / "optimization.db", load_taiwei_plugin=False)
    parameter_mismatches = []
    for key, run_ids in frozen["run_groups"].items():
        design, raw_index = key.split(":"); vector = frozen["vectors"][design][int(raw_index)]
        for run_id in run_ids:
            actual = state.runtime_store.get_run(run_id).task_spec.parameters
            for name, expected in vector.items():
                if name not in actual or not _same(actual[name], expected):
                    parameter_mismatches.append({"run_id": run_id, "parameter": name,
                                                 "expected": expected,
                                                 "actual": actual.get(name)})
            if not _same(actual.get("clock_period_ns"), 10.0):
                parameter_mismatches.append({"run_id": run_id,
                                             "parameter": "clock_period_ns",
                                             "expected": 10.0,
                                             "actual": actual.get("clock_period_ns")})
    bo_rows = {row["design"]: row for row in bo["design_rows"]}
    random_rows = {row["design"]: row for row in random_report["design_rows"]}
    comparisons = []
    for design in sorted(bo_rows):
        bo_row, random_row = bo_rows[design], random_rows[design]
        bo_utility, random_utility = float(bo_row["best_utility"]), float(random_row["best_utility"])
        comparisons.append({
            "design": design, "bo_best_utility": bo_utility,
            "random_best_utility": random_utility,
            "bo_minus_random": bo_utility - random_utility,
            "winner": "bo" if bo_utility > random_utility else
                      "random" if random_utility > bo_utility else "tie",
            "bo_met_threshold": bo_row["met_practical_threshold"],
            "random_met_threshold": random_row["met_practical_threshold"],
            "bo_failure_rate": bo_row["failure_rate"],
            "random_failure_rate": random_row["baseline"]["summary"]["failure_rate"]
                if all(item["summary"]["failure_rate"] == 0
                       for item in [random_row["baseline"], *random_row["candidates"]]) else
                max(item["summary"]["failure_rate"]
                    for item in [random_row["baseline"], *random_row["candidates"]]),
        })
    checks = {
        "same_fixed_four_designs": set(bo_rows) == set(random_rows)
            == {"gcd", "fifo", "uart_tx", "ibex_alu"},
        "equal_candidate_budget": all(row["runs"] == 12 for row in bo_rows.values())
            and all(len(row["candidates"]) == 3 for row in random_rows.values()),
        "three_repetitions_per_vector": all(
            len(run_ids) == 3 for run_ids in frozen["run_groups"].values()),
        "identical_clock_constraint": not parameter_mismatches,
        "identical_parameter_ranges": random_report["ranges"]
            == {"core_utilization_pct": [20.0, 65.0], "place_density": [.35, .75]},
        "all_bo_protocol_audits_passed": bo.get("status") == "passed",
        "all_random_runs_succeeded": random_report.get("failure_rate") == 0,
    }
    bo_wins = sum(row["winner"] == "bo" for row in comparisons)
    random_wins = sum(row["winner"] == "random" for row in comparisons)
    result = {
        "schema_version": 1, "kind": "v2_parameter_policy_ablation",
        "status": "passed" if all(checks.values()) else "failed", "checks": checks,
        "parameter_mismatches": parameter_mismatches,
        "comparisons": comparisons, "bo_wins": bo_wins,
        "random_wins": random_wins,
        "bo_designs_meeting_threshold": sum(row["bo_met_threshold"] for row in comparisons),
        "random_designs_meeting_threshold": sum(
            row["random_met_threshold"] for row in comparisons),
        "claim_boundary": (
            "BO beats the one-seed random comparator on the recorded fixed suite and equal "
            "three-candidate budget. Multiple random seeds are still required for a "
            "statistical-significance or general-superiority claim."),
    }
    destination = args.output.expanduser().resolve(); destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2),
                           encoding="utf-8")
    print(json.dumps({"output": str(destination), "status": result["status"],
                      "bo_wins": bo_wins, "random_wins": random_wins,
                      "bo_threshold": result["bo_designs_meeting_threshold"],
                      "random_threshold": result["random_designs_meeting_threshold"]},
                     ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
