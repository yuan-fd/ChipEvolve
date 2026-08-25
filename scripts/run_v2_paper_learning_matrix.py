#!/usr/bin/env python3
"""Run every ordered cross-design causal holdout registered for the v2 paper."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import itertools
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2),
                         encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit("output must be a new or empty directory")
    output.mkdir(parents=True, exist_ok=True)
    protocol_path = ROOT / "experiments/v2-paper-20260825/learning-protocol.json"
    protocol_bytes = protocol_path.read_bytes()
    (output / "protocol.snapshot.json").write_bytes(protocol_bytes)
    protocol = json.loads(protocol_bytes); study = protocol
    designs = tuple(study["designs"])
    pairs = tuple(itertools.permutations(designs, 2))
    if len(pairs) != int(study["ordered_source_holdout_pairs"]):
        raise SystemExit("ordered pair matrix differs from frozen protocol")
    rows = []; launched_at = datetime.now(timezone.utc).isoformat()
    for index, (source, holdout) in enumerate(pairs):
        destination = output / f"{source}--{holdout}"
        log_path = output / f"{source}--{holdout}.log"
        seed = 51001 + index
        command = [
            sys.executable, str(ROOT / "scripts/run_v2_causal_holdout.py"),
            "--output", str(destination), "--source-design", source,
            "--holdout-design", holdout, "--repetitions", "3",
            "--max-parallel", "12", "--seed", str(seed),
        ]
        started = datetime.now(timezone.utc).isoformat()
        execution_error = None
        with log_path.open("w", encoding="utf-8") as log:
            try:
                completed = subprocess.run(command, cwd=ROOT, text=True, stdout=log,
                                           stderr=subprocess.STDOUT, timeout=14_400, check=False)
            except subprocess.TimeoutExpired as exc:
                completed = None
                execution_error = f"TimeoutExpired after {exc.timeout} seconds"
                log.write(execution_error + "\n")
            if completed is not None and completed.returncode == 0:
                try:
                    audited = subprocess.run([
                        sys.executable, str(ROOT / "scripts/audit_v2_causal_holdout.py"),
                        "--experiment", str(destination),
                    ], cwd=ROOT, text=True, stdout=log, stderr=subprocess.STDOUT,
                        timeout=900, check=False)
                except subprocess.TimeoutExpired as exc:
                    audited = None
                    execution_error = f"Auditor TimeoutExpired after {exc.timeout} seconds"
                    log.write(execution_error + "\n")
            else:
                audited = None
        report = json.loads((destination / "report.json").read_text(encoding="utf-8")) \
            if (destination / "report.json").is_file() else {}
        validation = report.get("validation") or {}
        row = {
            "source": source, "holdout": holdout, "seed": seed,
            "started_at": started, "ended_at": datetime.now(timezone.utc).isoformat(),
            "returncode": completed.returncode if completed is not None else None,
            "audit_returncode": audited.returncode if audited is not None else None,
            "accepted": completed is not None and completed.returncode == 0
                        and audited is not None and audited.returncode == 0,
            "error": execution_error,
            "outcome": (validation.get("validation") or {}).get("outcome"),
            "knowledge_status": (validation.get("knowledge_card") or {}).get("status"),
            "source_interaction": (report.get("source_report") or {}).get("interaction_effect"),
            "holdout_interaction": (validation.get("holdout") or {}).get("interaction_effect"),
            "destination": str(destination), "log": str(log_path),
        }
        rows.append(row)
        _write(output / "matrix-progress.json", {
            "schema_version": 1, "protocol_id": protocol["protocol_id"],
            "protocol_sha256": hashlib.sha256(protocol_bytes).hexdigest(),
            "launched_at": launched_at, "expected_pairs": len(pairs), "completed": rows,
        })
    # Reconcile every pair with the current independent auditor. A transient
    # auditor defect must not trigger new EDA runs or discard raw outcomes.
    for row in rows:
        with Path(row["log"]).open("a", encoding="utf-8") as log:
            if not Path(row["destination"], "report.json").is_file():
                audited = None
                log.write("final reconciliation skipped: report.json is absent\n")
            else:
                try:
                    audited = subprocess.run([
                        sys.executable, str(ROOT / "scripts/audit_v2_causal_holdout.py"),
                        "--experiment", row["destination"],
                    ], cwd=ROOT, text=True, stdout=log, stderr=subprocess.STDOUT,
                        timeout=900, check=False)
                except subprocess.TimeoutExpired as exc:
                    audited = None
                    row["error"] = f"Final auditor TimeoutExpired after {exc.timeout} seconds"
                    log.write(row["error"] + "\n")
        row["audit_returncode"] = audited.returncode if audited is not None else None
        row["accepted"] = (row["returncode"] == 0 and audited is not None
                           and audited.returncode == 0)
    result = {
        "schema_version": 1, "kind": "v2_paper_learning_matrix",
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": hashlib.sha256(protocol_bytes).hexdigest(),
        "launched_at": launched_at, "ended_at": datetime.now(timezone.utc).isoformat(),
        "expected_pairs": len(pairs), "accepted_pairs": sum(row["accepted"] for row in rows),
        "status": "passed" if all(row["accepted"] for row in rows) else "failed",
        "pairs": rows,
    }
    _write(output / "matrix-report.json", result)
    print(json.dumps({"output": str(output / "matrix-report.json"),
                      "status": result["status"], "accepted_pairs": result["accepted_pairs"]},
                     ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
