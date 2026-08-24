#!/usr/bin/env python3
"""Fast deterministic ORFS protocol double for closed-loop integration tests."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    task = json.loads(args.request.read_text(encoding="utf-8"))["task"]
    params = task["parameters"]
    util = float(params["core_utilization_pct"])
    density = float(params["place_density"])
    period = float(params["clock_period_ns"])
    replica = int(task.get("labels", {}).get("replica_index", 0))
    jitter = (-1.0, 0.0, 1.0)[replica % 3]

    # Smooth coupled response surface with deterministic replica noise.
    area = 900.0 + (util - 43.0) ** 2 * 0.8 + (density - 0.61) ** 2 * 800 + jitter
    slack = period - 5.0 - (util - 40.0) ** 2 * 0.002 - abs(density - 0.58) * 0.5
    power = 0.08 + util * 0.0005 + density * 0.01 + jitter * 0.0001
    metrics = {
        "area_um2": area, "setup_wns_ns": slack, "power_W": power,
        "drc_errors": 0.0, "runtime_seconds": 0.01,
    }

    artifact_root = args.result.parent / "orfs" / "implementation"
    analysis = artifact_root / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    report = analysis / "report.json"
    report.write_text(json.dumps({"kpi": metrics, "runtime_seconds": 0.01}),
                      encoding="utf-8")
    artifacts = [{"kind": "report", "path": "orfs/implementation/analysis/report.json"}]
    for kind, relative in {
        "odb": "orfs/implementation/final.odb",
        "config": "orfs/implementation/config.mk",
        "toolchain_snapshot": "orfs/implementation/toolchain.json",
        "run_result": "orfs/implementation/run-result.json",
        "def": "orfs/implementation/final.def",
        "netlist": "orfs/implementation/final.v",
        "gds": "orfs/implementation/final.gds",
    }.items():
        path = args.result.parent / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(kind, encoding="utf-8")
        artifacts.append({"kind": kind, "path": relative})

    args.result.write_text(json.dumps({
        "schema_version": 1, "status": "succeeded", "exit_code": 0,
        "started_at": now(), "ended_at": now(),
        "metrics": [{"name": key, "value": value,
                     "unit": {"area_um2": "um2", "setup_wns_ns": "ns",
                              "power_W": "W", "drc_errors": "count",
                              "runtime_seconds": "s"}[key]}
                    for key, value in metrics.items()],
        "artifacts": artifacts, "failure": None,
        "provenance": {"adapter": "closed-loop-test-double"},
    }), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
