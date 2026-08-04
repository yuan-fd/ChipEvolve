#!/usr/bin/env python3
"""Deterministic adapter fixture for protocol conformance tests."""

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
    started = now()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    task = request["task"]
    report = args.result.parent / "report.json"
    report.write_text(
        json.dumps({"message": task["inputs"].get("message"), "task_id": task["task_id"]}),
        encoding="utf-8",
    )
    args.result.write_text(json.dumps({
        "schema_version": 1,
        "status": "succeeded",
        "exit_code": 0,
        "started_at": started,
        "ended_at": now(),
        "metrics": [{"name": "messages", "value": 1, "unit": "count"}],
        "artifacts": [{"kind": "report", "path": "report.json"}],
        "failure": None,
        "provenance": {"adapter": "echo-fixture"},
    }), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
