#!/usr/bin/env python3
"""Independent acceptance audit for a persisted v2 closed-loop experiment."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app import ApiState  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--orfs-root", type=Path,
                        default=Path("/share/home/yuanwenjie/OpenROAD-flow-scripts"))
    args = parser.parse_args()
    root = args.experiment.expanduser().resolve()
    report_path = root / "report.json"
    if not report_path.is_file() or not (root / "runtime.db").is_file():
        raise SystemExit("experiment requires report.json and runtime.db")
    source = json.loads(report_path.read_text(encoding="utf-8"))
    state = ApiState(
        root / "platform.db", root / "uploads", args.orfs_root,
        design_root=root / "designs", legacy_root=root / "legacy",
        runtime_db_path=root / "runtime.db",
        optimization_db_path=root / "optimization.db", load_taiwei_plugin=False,
    )
    rows = []
    for summary in state.list_runtime_runs(limit=500)["runs"]:
        run = state.runtime_store.get_run(summary["run_id"])
        task = run.task_spec
        rows.append({
            "run_id": run.run_id, "status": run.status.value,
            "task_id": task.task_id,
            "rtl_sha256": (task.inputs.get("rtl") or {}).get("sha256"),
            "top": task.inputs.get("top"), "clock": task.inputs.get("clock"),
            "parameters": dict(task.parameters),
        })
    checkpoint = (source.get("checkpoint") or {}).get("state") or {}
    repetitions = int(checkpoint.get("repetitions") or 0)
    history = checkpoint.get("history") or []
    clock_values = sorted({float(row["parameters"].get("clock_period_ns")) for row in rows})
    immutable_contexts = {
        (row["rtl_sha256"], row["top"], row["clock"],
         row["parameters"].get("platform"), row["parameters"].get("target_stage"),
         row["parameters"].get("clock_period_ns")) for row in rows
    }
    vectors = Counter((float(row["parameters"].get("core_utilization_pct")),
                       float(row["parameters"].get("place_density"))) for row in rows)
    summaries_complete = all(
        item.get("summary", {}).get("replicas") == repetitions
        and len(item.get("summary", {}).get("run_ids") or []) == repetitions
        and all(
            isinstance(metric.get("median"), (int, float))
            and math.isfinite(float(metric["median"]))
            and all(key in metric for key in ("q1", "q3", "iqr", "minimum", "maximum"))
            for metric in item.get("summary", {}).get("metrics", {}).values()
        ) for item in history
    )
    checks = {
        "terminal_statuses_complete": bool(rows) and all(
            row["status"] in {"succeeded", "failed", "cancelled", "timed_out", "lost"}
            for row in rows),
        "clock_constraint_frozen": len(clock_values) == 1,
        "immutable_context_frozen": len(immutable_contexts) == 1,
        "each_parameter_vector_repeated": bool(vectors) and repetitions >= 2
            and all(count == repetitions for count in vectors.values()),
        "history_covers_all_runs": sum(
            len(item.get("summary", {}).get("run_ids") or []) for item in history
        ) == len(rows),
        "variation_statistics_present": summaries_complete,
        "hard_constraints_recorded": bool(history) and all(
            item.get("summary", {}).get("constraints") for item in history),
        "failure_rates_recorded": bool(history) and all(
            isinstance(item.get("summary", {}).get("failure_rate"), (int, float))
            for item in history),
        "claim_boundary_recorded": bool(source.get("claim_boundary")),
    }
    audit = {
        "schema_version": 1, "kind": "v2_closed_loop_protocol_audit",
        "source_report": str(report_path), "pipeline_id": source.get("pipeline_id"),
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks, "run_count": len(rows), "repetitions": repetitions,
        "round_count": max(0, len(history) - 1),
        "clock_period_ns_values": clock_values,
        "terminal_status_counts": dict(sorted(Counter(
            row["status"] for row in rows).items())),
        "parameter_vector_counts": [
            {"core_utilization_pct": key[0], "place_density": key[1], "count": count}
            for key, count in sorted(vectors.items())
        ],
        "run_rows": rows,
        "invalid_claim_rule": (
            "Any experiment with more than one clock_period_ns value fails this audit and "
            "cannot support a PPA-improvement claim."
        ),
    }
    destination = root / "protocol-audit.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2),
                         encoding="utf-8")
    temporary.replace(destination)
    print(json.dumps({"output": str(destination), "status": audit["status"],
                      "checks": checks}, ensure_ascii=False))
    return 0 if audit["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
