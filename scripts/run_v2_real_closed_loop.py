#!/usr/bin/env python3
"""Run the v2 autonomous BO/GP loop on real ORFS and persist raw evidence.

This script is intentionally not a simulator. It constructs the same ApiState,
WorkflowRuntime and ORFS plugin used by the product, then records checkpoint,
Runtime and optimization-store identities for independent inspection.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app import ApiState  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--design", choices=("gcd", "fifo", "uart_tx", "ibex_alu"),
                        default="gcd")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260824)
    parser.add_argument("--orfs-root", type=Path,
                        default=Path("/share/home/yuanwenjie/OpenROAD-flow-scripts"))
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit("output must be a new or empty directory")
    output.mkdir(parents=True, exist_ok=True)

    package = ROOT / "benchmarks" / "v2" / args.design
    manifest = json.loads((package / "package.json").read_text(encoding="utf-8"))
    state = ApiState(
        output / "platform.db", output / "uploads", args.orfs_root,
        design_root=output / "designs", legacy_root=output / "legacy",
        runtime_db_path=output / "runtime.db",
        optimization_db_path=output / "optimization.db",
        load_taiwei_plugin=False,
    )
    health = state.health()
    if not health["execution_ready"]:
        raise SystemExit(f"real ORFS is not ready: {health}")
    design = state.designs.import_rtl(
        filename=manifest["golden_rtl"],
        source=(package / manifest["golden_rtl"]).read_text(encoding="utf-8"),
        description=f"v2 real closed-loop fixture: {args.design}",
        owner_id=None,
    )
    created = state.start_bayesian_closed_loop({
        "design_id": design["id"],
        "experiment_key": f"real-{args.design}-bo-gp",
        "objective_profile": "balanced",
        "repetitions": args.repetitions,
        "max_rounds": args.max_rounds,
        "stall_window": 3,
        "minimum_relative_improvement": .005,
        "target_stage": "finish",
        "platform": "nangate45",
        "clock": manifest.get("clock"),
        "clock_period_ns": 10.0,
        "core_utilization_pct": 30.0,
        "place_density": .55,
        "parameter_space": {
            "core_utilization_pct": [20.0, 65.0],
            "place_density": [.35, .75],
        },
        "max_parallel": args.repetitions,
    })
    result = state.run_bayesian_closed_loop_to_boundary(
        created["pipeline_id"], {"max_transitions": 32, "seed": args.seed})
    runtime_runs = state.list_runtime_runs(limit=100)["runs"]
    task_rows = []
    for item in runtime_runs:
        task = state.runtime_store.get_run(item["run_id"]).task_spec
        task_rows.append({
            "run_id": item["run_id"], "status": item["status"],
            "task_id": task.task_id, "parameters": dict(task.parameters),
        })
    clock_values = sorted({float(item["parameters"]["clock_period_ns"])
                           for item in task_rows})
    invariant_passed = len(clock_values) == 1
    report = {
        "schema_version": 1, "kind": "v2_real_closed_loop_report",
        "optimizer_seed": args.seed,
        "design": args.design, "design_record": design,
        "health": {key: health[key] for key in (
            "execution_ready", "orfs_ready", "openroad", "yosys")},
        "pipeline_id": created["pipeline_id"], "checkpoint": result,
        "runtime_runs": runtime_runs,
        "run_protocol_rows": task_rows,
        "comparison_invariants": {
            "clock_period_ns_values": clock_values,
            "clock_frozen": invariant_passed,
            "search_parameters": ["core_utilization_pct", "place_density"],
            "reason": "clock is a design constraint, not an optimization knob",
        },
        "study": state.optimization_store.describe(result["state"]["study_id"])
                 if result["state"].get("study_id") else None,
        "claim_boundary": (
            "real repeated ORFS evidence for this design/configuration only; "
            "not a cross-design or statistical-significance claim"),
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8")
    print(json.dumps({
        "output": str(output), "pipeline_id": created["pipeline_id"],
        "status": result["state"]["status"],
        "rounds": result["state"]["round"],
        "runs": len(report["runtime_runs"]),
        "best_utility": result["state"]["best_utility"],
    }, ensure_ascii=False))
    return 0 if (invariant_passed and result["state"]["status"]
                 in {"completed", "diagnosis_required"}) else 1


if __name__ == "__main__":
    raise SystemExit(main())
