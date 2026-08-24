#!/usr/bin/env python3
"""Tiny upstream-shaped RTLScout CLI used to test the black-box boundary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("--benchmark", required=True)
parser.add_argument("--model", required=True)
parser.add_argument("--runs-dir", type=Path, required=True)
parser.add_argument("--max-steps")
parser.add_argument("--cost-metric", required=True)
parser.add_argument("--dont-save-workspaces", action="store_true")
parser.add_argument("--benchmarks-root", nargs="+")
args = parser.parse_args()

root = args.runs_dir / args.benchmark / args.model.replace(":", "_") / "session"
(root / "best_design").mkdir(parents=True)
passed = "fail" not in args.model
module_name = "generated_top"
if args.benchmarks_root:
    metadata = json.loads((Path(args.benchmarks_root[0]) / args.benchmark / "metadata.json").read_text())
    module_name = metadata["module_name"]
if passed:
    (root / "best_design" / "design.sv").write_text(
        f"module {module_name}(input [1:0] a, output [1:0] y); assign y = a; endmodule\n",
        encoding="utf-8",
    )
(root / "summary.txt").write_text(
    f"fake RTLScout passed={passed}\n", encoding="utf-8"
)
(root / "result.json").write_text(json.dumps({
    "benchmark_name": args.benchmark,
    "model": args.model.split(":", 1)[1],
    "passed": passed,
    "best_cost": 12 if passed else None,
    "cost_metric": args.cost_metric,
    "best_metrics": {"transistors": 12, "num_cells": 2} if passed else {},
    "error": "fixture failure" if not passed else "",
    "workdir": str(root),
}), encoding="utf-8")
print("Best: PASS" if passed else "Best: FAIL")
