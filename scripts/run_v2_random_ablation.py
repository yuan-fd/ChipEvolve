#!/usr/bin/env python3
"""Run an equal-budget seeded-random comparator for the v2 BO/GP suite."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import random
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app import ApiState  # noqa: E402
from openroad_platform_analysis import (  # noqa: E402
    RuntimeEvidenceExporter, relative_utility, summarize_replicates,
)
from openroad_platform_execution import build_orfs_task  # noqa: E402


DESIGNS = ("gcd", "fifo", "uart_tx", "ibex_alu")
REPETITIONS = 3
DEFAULT_SEED = 20260825
RANGES = {"core_utilization_pct": (20.0, 65.0), "place_density": (.35, .75)}


def _vectors(design: str, seed: int) -> list[dict[str, float]]:
    # All random candidates are frozen before any EDA result exists.
    offset = int(hashlib.sha256(design.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed + offset)
    return [
        {"core_utilization_pct": 30.0, "place_density": .55},
        *[{"core_utilization_pct": rng.uniform(*RANGES["core_utilization_pct"]),
           "place_density": rng.uniform(*RANGES["place_density"])}
          for _ in range(3)],
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-parallel", type=int, default=12)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--orfs-root", type=Path,
                        default=Path("/share/home/yuanwenjie/OpenROAD-flow-scripts"))
    args = parser.parse_args()
    if not 1 <= args.max_parallel <= 32:
        raise SystemExit("max-parallel must be 1-32")
    output = args.output.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit("output must be a new or empty directory")
    output.mkdir(parents=True, exist_ok=True)
    state = ApiState(
        output / "platform.db", output / "uploads", args.orfs_root,
        design_root=output / "designs", legacy_root=output / "legacy",
        runtime_db_path=output / "runtime.db",
        optimization_db_path=output / "optimization.db", load_taiwei_plugin=False)
    if not state.health()["execution_ready"]:
        raise SystemExit("real ORFS execution is unavailable")

    frozen_plan, design_records, run_groups = {}, {}, {}
    all_run_ids = []
    for design_name in DESIGNS:
        package = ROOT / "benchmarks" / "v2" / design_name
        manifest = json.loads((package / "package.json").read_text(encoding="utf-8"))
        record = state.designs.import_rtl(
            filename=f"{design_name}.sv",
            source=(package / manifest["golden_rtl"]).read_text(encoding="utf-8"),
            description=f"v2 seeded-random ablation: {design_name}", owner_id=None)
        design_records[design_name] = record
        vectors = _vectors(design_name, args.seed); frozen_plan[design_name] = vectors
        for vector_index, vector in enumerate(vectors):
            key = (design_name, vector_index); run_groups[key] = []
            for replica in range(REPETITIONS):
                task = build_orfs_task(
                    state.designs.rtl_path(record["id"]), project_id="openroad-platform",
                    design_id=record["id"], top=manifest["top"], clock="clk",
                    platform_name="nangate45", target_stage="finish",
                    clock_period_ns=10.0,
                    core_utilization_pct=vector["core_utilization_pct"],
                    place_density=vector["place_density"],
                    labels={"v2_research": "seeded-random-ablation",
                            "random_seed": str(args.seed), "vector_index": str(vector_index),
                            "replica_index": str(replica)},
                )
                run_id = state.runtime.submit(task).run_id
                run_groups[key].append(run_id); all_run_ids.append(run_id)
    # Only after the complete frozen plan has been materialized do tools run.
    (output / "frozen-plan.json").write_text(json.dumps({
        "schema_version": 1, "kind": "v2_seeded_random_frozen_plan",
        "seed": args.seed, "designs": list(DESIGNS), "ranges": RANGES,
        "repetitions": REPETITIONS, "vectors": frozen_plan,
        "run_groups": {f"{key[0]}:{key[1]}": value for key, value in run_groups.items()},
    }, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    with ThreadPoolExecutor(max_workers=min(args.max_parallel, len(all_run_ids))) as pool:
        for result in pool.map(state.runtime.execute_once, all_run_ids):
            del result

    objectives = state._v2_objectives("balanced")
    hard_constraints = (
        {"metric": "setup_wns_ns", "operator": ">=", "threshold": 0.0},
        {"metric": "drc_errors", "operator": "<=", "threshold": 0.0},
    )
    exporter = RuntimeEvidenceExporter(state.runtime_store)
    rows = []
    for design_name in DESIGNS:
        summaries = []
        for vector_index, vector in enumerate(frozen_plan[design_name]):
            run_ids = run_groups[(design_name, vector_index)]
            context = state._learning_context_for_run(state.runtime_store.get_run(run_ids[0]))
            observations = [exporter.export_run(run_id, context) for run_id in run_ids]
            summary = summarize_replicates(observations, objectives, hard_constraints)
            summaries.append({"vector_index": vector_index, "parameters": vector,
                              "run_ids": run_ids, "summary": summary})
        baseline = summaries[0]["summary"]
        for item in summaries:
            item["utility"] = relative_utility(item["summary"], baseline, objectives)
        best = max(summaries, key=lambda item: float(item["utility"] or -1e100))
        rows.append({
            "design": design_name, "design_record": design_records[design_name],
            "baseline": summaries[0], "candidates": summaries[1:], "best": best,
            "best_utility": best["utility"],
            "met_practical_threshold": bool(best["utility"] is not None
                                             and best["utility"] >= .005),
        })
    terminal = [state.runtime_store.get_run(run_id).status.value for run_id in all_run_ids]
    report = {
        "schema_version": 1, "kind": "v2_equal_budget_seeded_random_ablation",
        "seed": args.seed, "ranges": RANGES, "repetitions": REPETITIONS,
        "candidate_budget_per_design": 3, "design_rows": rows,
        "run_count": len(all_run_ids), "terminal_statuses": terminal,
        "failure_rate": sum(status != "succeeded" for status in terminal) / len(terminal),
        "claim_boundary": (
            "Seeded random is an equal new-evaluation comparator for the four fixed designs. "
            "It does not include hyperparameter tuning or claim statistical significance."),
    }
    destination = output / "report.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2),
                           encoding="utf-8")
    accepted = len(all_run_ids) == 48 and all(status == "succeeded" for status in terminal)
    print(json.dumps({"output": str(destination), "accepted": accepted,
                      "runs": len(all_run_ids),
                      "designs_meeting_threshold": sum(
                          row["met_practical_threshold"] for row in rows)},
                     ensure_ascii=False))
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
