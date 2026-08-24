#!/usr/bin/env python3
"""Run the four-design BO/GP closed-loop suite for one research seed."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
DESIGNS = ("gcd", "fifo", "uart_tx", "ibex_alu")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()
    root = args.output.expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise SystemExit("output must be a new or empty directory")
    root.mkdir(parents=True, exist_ok=True)

    def run(design: str) -> dict:
        destination = root / design
        command = [
            sys.executable, str(ROOT / "scripts/run_v2_real_closed_loop.py"),
            "--output", str(destination), "--design", design,
            "--repetitions", "3", "--max-rounds", "3", "--seed", str(args.seed),
        ]
        completed = subprocess.run(command, cwd=ROOT, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   timeout=7200, check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"{design} failed:\n{completed.stdout}\n{completed.stderr}")
        for verifier in (
            [sys.executable, str(ROOT / "scripts/audit_v2_closed_loop.py"),
             "--experiment", str(destination)],
            [sys.executable, str(ROOT / "scripts/export_v2_real_edair.py"),
             "--experiment", str(destination), "--focus", "diagnosis"],
        ):
            checked = subprocess.run(verifier, cwd=ROOT, text=True,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     timeout=600, check=False)
            if checked.returncode != 0:
                raise RuntimeError(f"{design} verifier failed:\n{checked.stdout}\n{checked.stderr}")
        report = json.loads((destination / "report.json").read_text(encoding="utf-8"))
        state = report["checkpoint"]["state"]
        return {"design": design, "experiment": str(destination),
                "pipeline_id": report["pipeline_id"], "status": state["status"],
                "best_utility": state["best_utility"], "best_round": state["best_round"],
                "runs": len(report["runtime_runs"]),
                "met_practical_threshold": state["best_utility"] >= .005}

    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(run, DESIGNS))
    report = {"schema_version": 1, "kind": "v2_bo_seed_suite",
              "seed": args.seed, "design_rows": rows,
              "run_count": sum(row["runs"] for row in rows),
              "designs_meeting_threshold": sum(
                  row["met_practical_threshold"] for row in rows),
              "claim_boundary": "one optimizer seed; combine multiple seed suites before policy claims"}
    (root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    accepted = report["run_count"] == 48 and all(
        row["status"] in {"completed", "diagnosis_required"} for row in rows)
    print(json.dumps({"output": str(root / "report.json"), "accepted": accepted,
                      "runs": report["run_count"],
                      "designs_meeting_threshold": report["designs_meeting_threshold"]},
                     ensure_ascii=False))
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
