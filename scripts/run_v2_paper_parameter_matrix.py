#!/usr/bin/env python3
"""Run the preregistered paired BO/GP-versus-random paper matrix."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SEEDS = tuple(range(41001, 41011))


def _write(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2),
                         encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-suites", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.max_suites <= 3:
        raise SystemExit("max-suites must be 1-3 to avoid ORFS oversubscription")
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    protocol_path = ROOT / "experiments/v2-paper-20260825/protocol.json"
    protocol_bytes = protocol_path.read_bytes()
    protocol_sha256 = hashlib.sha256(protocol_bytes).hexdigest()
    protocol = json.loads(protocol_bytes)
    if tuple(protocol["parameter_policy_primary"]["policy_seeds"]) != SEEDS:
        raise SystemExit("runner seed matrix differs from frozen protocol")
    snapshot = output / "protocol.snapshot.json"
    if snapshot.exists() and snapshot.read_bytes() != protocol_bytes:
        raise SystemExit("existing matrix was launched under a different frozen protocol")
    if not snapshot.exists():
        snapshot.write_bytes(protocol_bytes)
    tasks = [(seed, policy) for seed in SEEDS for policy in ("bo_gp", "seeded_random")]
    launched_at = datetime.now(timezone.utc).isoformat()

    def run(item: tuple[int, str]) -> dict:
        seed, policy = item
        destination = output / policy / f"seed-{seed}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        log_path = output / f"{policy}-seed-{seed}.log"
        if policy == "bo_gp":
            command = [
                sys.executable, str(ROOT / "scripts/run_v2_bo_seed_suite.py"),
                "--output", str(destination), "--seed", str(seed),
            ]
        else:
            command = [
                sys.executable, str(ROOT / "scripts/run_v2_random_ablation.py"),
                "--output", str(destination), "--seed", str(seed),
                "--max-parallel", "12",
            ]
        started = datetime.now(timezone.utc).isoformat()
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                command, cwd=ROOT, text=True, stdout=log,
                stderr=subprocess.STDOUT, check=False,
            )
        report_path = destination / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) \
            if report_path.is_file() else None
        return {
            "seed": seed, "policy": policy, "returncode": completed.returncode,
            "started_at": started, "ended_at": datetime.now(timezone.utc).isoformat(),
            "destination": str(destination), "log": str(log_path),
            "report_present": report is not None,
            "run_count": (report or {}).get("run_count"),
            "accepted": completed.returncode == 0,
        }

    rows = []
    with ThreadPoolExecutor(max_workers=args.max_suites) as pool:
        futures = {pool.submit(run, item): item for item in tasks}
        for future in as_completed(futures):
            try:
                rows.append(future.result())
            except Exception as exc:
                seed, policy = futures[future]
                rows.append({
                    "seed": seed, "policy": policy, "accepted": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "ended_at": datetime.now(timezone.utc).isoformat(),
                })
            _write(output / "matrix-progress.json", {
                "schema_version": 1, "launched_at": launched_at,
                "protocol_sha256": protocol_sha256,
                "completed": sorted(rows, key=lambda row: (row["seed"], row["policy"])),
                "expected_cells": len(tasks),
            })
    rows.sort(key=lambda row: (row["seed"], row["policy"]))
    result = {
        "schema_version": 1, "kind": "v2_paper_parameter_matrix",
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": protocol_sha256, "launched_at": launched_at,
        "ended_at": datetime.now(timezone.utc).isoformat(), "cells": rows,
        "accepted_cells": sum(bool(row.get("accepted")) for row in rows),
        "expected_cells": len(tasks),
        "status": "passed" if all(row.get("accepted") for row in rows) else "failed",
        "claim_boundary": "Execution ledger only; statistical claims require the frozen postprocessor.",
    }
    _write(output / "matrix-report.json", result)
    print(json.dumps({"output": str(output / "matrix-report.json"),
                      "status": result["status"],
                      "accepted_cells": result["accepted_cells"]}, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
