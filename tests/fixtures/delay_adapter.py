#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--request", type=Path, required=True)
parser.add_argument("--result", type=Path, required=True)
args = parser.parse_args()
started = datetime.now(timezone.utc).isoformat()
task = json.loads(args.request.read_text(encoding="utf-8"))["task"]
time.sleep(float(task["parameters"].get("delay", 0.2)))
report = args.result.parent / "report.json"
report.write_text(json.dumps({"task_id": task["task_id"]}), encoding="utf-8")
args.result.write_text(json.dumps({
    "schema_version": 1, "status": "succeeded", "exit_code": 0,
    "started_at": started, "ended_at": datetime.now(timezone.utc).isoformat(),
    "metrics": [], "artifacts": [{"kind": "report", "path": "report.json"}],
    "failure": None, "provenance": {"fixture": "delay"},
}), encoding="utf-8")
