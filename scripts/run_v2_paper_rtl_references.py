#!/usr/bin/env python3
"""Run hidden golden RTL references under generated-RTL backend constraints."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app import ApiState  # noqa: E402
from openroad_platform_analysis import paired_replica_seeds  # noqa: E402
from openroad_platform_execution import build_orfs_task  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-parallel", type=int, default=6)
    args = parser.parse_args()
    if not 1 <= args.max_parallel <= 8:
        raise SystemExit("max-parallel must be 1-8")
    output = args.output.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit("output must be a new or empty directory")
    output.mkdir(parents=True, exist_ok=True)
    protocol_path = ROOT / "experiments/v2-paper-20260825/rtl-reference-protocol.json"
    protocol_bytes = protocol_path.read_bytes(); protocol = json.loads(protocol_bytes)
    (output / "protocol.snapshot.json").write_bytes(protocol_bytes)
    state = ApiState(output / "platform.db", output / "uploads",
                     Path("/share/home/yuanwenjie/OpenROAD-flow-scripts"),
                     design_root=output / "designs", legacy_root=output / "legacy",
                     runtime_db_path=output / "runtime.db",
                     optimization_db_path=output / "optimization.db", load_taiwei_plugin=False)
    seeds = paired_replica_seeds(protocol["paired_or_seed_experiment_seed"], protocol["replicas"])
    groups = {}; all_ids = []
    for design in protocol["designs"]:
        package = ROOT / "benchmarks/v2" / design
        manifest = json.loads((package / "package.json").read_text())
        record = state.designs.import_rtl(filename=f"{design}.sv",
            source=(package / manifest["golden_rtl"]).read_text(),
            description=f"hidden paper reference: {design}", owner_id=None)
        ids = []
        for replica, seed in enumerate(seeds):
            task = build_orfs_task(state.designs.rtl_path(record["id"]),
                project_id="openroad-platform", design_id=record["id"], top=manifest["top"],
                clock="clk", platform_name=protocol["platform"], target_stage="finish",
                clock_period_ns=protocol["clock_period_ns"],
                core_utilization_pct=protocol["core_utilization_pct"],
                place_density=protocol["place_density"], or_seed=seed,
                timeout_seconds=28_800, stage_timeout_seconds=14_400,
                labels={"paper_reference": "hidden_golden", "design": design,
                        "replica_index": str(replica), "or_seed": str(seed)})
            run = state.runtime.submit(task); ids.append(run.run_id); all_ids.append(run.run_id)
        groups[design] = ids
    with ThreadPoolExecutor(max_workers=args.max_parallel) as pool:
        list(pool.map(state.runtime.execute_once, all_ids))
    rows = []
    for design, ids in groups.items():
        views = [state.get_runtime_run(run_id) for run_id in ids]
        metrics = {}
        for metric in protocol["metrics"]:
            values = []
            for view in views:
                report = (view.get("analysis_report") or {}).get("report") or {}
                value = (report.get("kpi") or {}).get(metric)
                if isinstance(value, (int, float)):
                    values.append(value)
            metrics[metric] = {"values": values,
                               "minimum": min(values) if values else None,
                               "maximum": max(values) if values else None,
                               "median": sorted(values)[len(values)//2] if values else None}
        rows.append({"design": design, "run_ids": ids,
                     "statuses": [view["run"]["status"] for view in views], "metrics": metrics})
    result = {"schema_version": 1, "kind": "v2_paper_rtl_hidden_reference",
              "protocol_id": protocol["protocol_id"],
              "protocol_sha256": hashlib.sha256(protocol_bytes).hexdigest(),
              "paired_or_seeds": list(seeds), "run_count": len(all_ids), "design_rows": rows,
              "status": "passed" if all(status == "succeeded" for row in rows for status in row["statuses"]) else "failed",
              "claim_boundary": protocol["claim_boundary"]}
    destination = output / "report.json"
    destination.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(destination), "status": result["status"],
                      "runs": result["run_count"]}, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
