#!/usr/bin/env python3
"""Example capability: summarize a list of numbers.

This is deliberately small and unrelated to chip design.  Its purpose is to be
the reference an author copies when writing a real adapter, so it demonstrates
every part of the protocol in the fewest lines:

* read ``--request`` (an immutable task) and write ``--result``
* emit stage progress on stdout using the marker the manifest declares
* declare artifacts as paths relative to the attempt workspace
* tie every metric to the artifact it was read from
* exit with the code the result claims

Two things it must never do, both enforced by the platform: claim success with
a non-zero exit code, and touch a path outside its workspace.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

PROGRESS_MARKER = "[progress]"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def report(stage: str, phase: str, **extra) -> None:
    """Emit one progress envelope. The platform reads the shape, not the names."""
    print(PROGRESS_MARKER + " " + json.dumps(
        {"stage": stage, "phase": phase, **extra}
    ), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()

    request_path = Path(args.request)
    result_path = Path(args.result)
    workspace = result_path.parent

    started_at = now()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    task = request["task"]
    records = task.get("inputs", {}).get("records")

    report("validate", "started")
    if not isinstance(records, list) or not records:
        report("validate", "finished", status="failed", seconds=0.0)
        _write(result_path, {
            "status": "failed",
            "exit_code": 2,
            "failure": {
                "category": "invalid_input",
                "message": "inputs.records must be a non-empty list",
                "retryable": False,
            },
        }, started_at)
        return 2
    try:
        values = [float(item) for item in records]
    except (TypeError, ValueError) as exc:
        report("validate", "finished", status="failed", seconds=0.0)
        _write(result_path, {
            "status": "failed",
            "exit_code": 2,
            "failure": {
                "category": "invalid_input",
                "message": f"records must be numeric: {exc}",
                "retryable": False,
            },
        }, started_at)
        return 2
    report("validate", "finished", status="succeeded", seconds=0.0)

    report("summarize", "started")
    summary = {
        "count": len(values),
        "total": sum(values),
        "mean": statistics.fmean(values),
        "minimum": min(values),
        "maximum": max(values),
        "stdev": statistics.pstdev(values) if len(values) > 1 else 0.0,
    }
    (workspace / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    (workspace / "run.log").write_text(
        f"summarized {len(values)} records\n", encoding="utf-8"
    )
    report("summarize", "finished", status="succeeded", seconds=0.0)

    _write(result_path, {
        "status": "succeeded",
        "exit_code": 0,
        "artifacts": [
            {"kind": "summary", "path": "summary.json"},
            {"kind": "log", "path": "run.log"},
        ],
        "metrics": [
            {"name": "record_count", "value": summary["count"],
             "context": {"source_artifact_store_key": "summary.json",
                         "parser_id": "example-reporter",
                         "parser_version": "1.0.0"}},
            {"name": "mean", "value": summary["mean"],
             "context": {"source_artifact_store_key": "summary.json",
                         "parser_id": "example-reporter",
                         "parser_version": "1.0.0"}},
        ],
    }, started_at)
    return 0


def _write(path: Path, payload: dict, started_at: str) -> None:
    payload = {
        "schema_version": 2,
        "started_at": started_at,
        "ended_at": now(),
        **payload,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
