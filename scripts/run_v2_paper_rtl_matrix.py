#!/usr/bin/env python3
"""Run the frozen repeated natural-language RTL-to-GDS paper matrix."""
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


def _write(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-attempts", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.max_attempts <= 2:
        raise SystemExit("max-attempts must be 1 or 2 to bound Codex and ORFS load")
    output = args.output.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit("output must be a new or empty directory")
    output.mkdir(parents=True, exist_ok=True)
    protocol_path = ROOT / "experiments/v2-paper-20260825/rtl-protocol.json"
    protocol_bytes = protocol_path.read_bytes(); protocol = json.loads(protocol_bytes)
    (output / "protocol.snapshot.json").write_bytes(protocol_bytes)
    tasks = [(design, attempt) for design in protocol["designs"]
             for attempt in range(1, int(protocol["attempts_per_design"]) + 1)]
    launched_at = datetime.now(timezone.utc).isoformat()

    def run(item: tuple[str, int]) -> dict:
        design, attempt = item
        destination = output / design / f"attempt-{attempt:02d}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        log_path = output / f"{design}-attempt-{attempt:02d}.log"
        command = [sys.executable, str(ROOT / "scripts/run_v2_real_rtl_pipeline.py"),
                   "--output", str(destination), "--design", design]
        started = datetime.now(timezone.utc).isoformat()
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(command, cwd=ROOT, text=True, stdout=log,
                                       stderr=subprocess.STDOUT, timeout=14_400, check=False)
        report = json.loads((destination / "report.json").read_text(encoding="utf-8")) \
            if (destination / "report.json").is_file() else {}
        return {"design": design, "attempt": attempt, "returncode": completed.returncode,
                "passed": completed.returncode == 0 and report.get("status") == "passed",
                "pipeline_status": (report.get("pipeline") or {}).get("status"),
                "started_at": started, "ended_at": datetime.now(timezone.utc).isoformat(),
                "destination": str(destination), "log": str(log_path)}

    rows = []
    with ThreadPoolExecutor(max_workers=args.max_attempts) as pool:
        futures = {pool.submit(run, item): item for item in tasks}
        for future in as_completed(futures):
            design, attempt = futures[future]
            try:
                rows.append(future.result())
            except Exception as exc:
                rows.append({"design": design, "attempt": attempt, "passed": False,
                             "error": f"{type(exc).__name__}: {exc}",
                             "ended_at": datetime.now(timezone.utc).isoformat()})
            _write(output / "matrix-progress.json", {
                "schema_version": 1, "protocol_id": protocol["protocol_id"],
                "protocol_sha256": hashlib.sha256(protocol_bytes).hexdigest(),
                "launched_at": launched_at, "expected_attempts": len(tasks),
                "completed": sorted(rows, key=lambda x: (x["design"], x["attempt"])),
            })
    rows.sort(key=lambda x: (x["design"], x["attempt"]))
    result = {"schema_version": 1, "kind": "v2_paper_rtl_matrix",
              "protocol_id": protocol["protocol_id"],
              "protocol_sha256": hashlib.sha256(protocol_bytes).hexdigest(),
              "launched_at": launched_at, "ended_at": datetime.now(timezone.utc).isoformat(),
              "expected_attempts": len(tasks), "passed_attempts": sum(x["passed"] for x in rows),
              "status": "complete", "attempts": rows,
              "claim_boundary": "Execution ledger only; failures are valid outcomes, so matrix completion does not require every RTL attempt to pass."}
    _write(output / "matrix-report.json", result)
    print(json.dumps({"output": str(output / "matrix-report.json"),
                      "passed_attempts": result["passed_attempts"],
                      "expected_attempts": result["expected_attempts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
