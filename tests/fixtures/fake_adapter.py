#!/usr/bin/env python3
"""A fake capability used to exercise the adapter protocol.

It is driven entirely by ``task.inputs["behaviour"]`` so one file can play every
role: an honest adapter, a liar, a silent failure, a path escaper.  One file
also means the behaviours cannot drift apart between tests.

The kernel rules apply here too (this lives under core/), so nothing in this
file names a real tool or vendor.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def emit(stage: str, phase: str, **extra) -> None:
    payload = {"stage": stage, "phase": phase, **extra}
    print("[progress] " + json.dumps(payload), flush=True)


def write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def ok(request: dict, result_path: Path, workspace: Path) -> int:
    (workspace / "report.json").write_text('{"area_um2": 1234.5}', encoding="utf-8")
    (workspace / "run.log").write_text("did the thing\n", encoding="utf-8")
    emit("alpha", "started")
    emit("alpha", "finished", status="succeeded", seconds=1.5)
    emit("beta", "started")
    emit("beta", "finished", status="succeeded", seconds=0.25)
    write(result_path, {
        "schema_version": 3,
        "status": "succeeded",
        "exit_code": 0,
        "started_at": "2026-01-01T00:00:00+00:00",
        "ended_at": "2026-01-01T00:00:02+00:00",
        "artifacts": [
            {"kind": "report", "path": "report.json"},
            {"kind": "log", "path": "run.log"},
        ],
        "metrics": [
            {"name": "area_um2", "value": 1234.5, "unit": "um2",
             "context": {"source_artifact_store_key": "report.json",
                         "parser_id": "fake", "parser_version": "1"}},
        ],
    })
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()

    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    task = request["task"]
    behaviour = (task.get("inputs") or {}).get("behaviour", "ok")
    result_path = Path(args.result)
    workspace = result_path.parent

    if behaviour == "ok":
        return ok(request, result_path, workspace)

    if behaviour == "no_result":
        # Exits cleanly but never produces the result file.
        return 0

    if behaviour == "lie_success_nonzero_exit":
        write(result_path, {
            "schema_version": 3, "status": "succeeded", "exit_code": 0,
            "started_at": "t0", "ended_at": "t1", "artifacts": [], "metrics": [],
        })
        return 1

    if behaviour == "exit_code_mismatch":
        write(result_path, {
            "schema_version": 3, "status": "failed", "exit_code": 7,
            "started_at": "t0", "ended_at": "t1",
            "failure": {"category": "tool_error", "message": "boom"},
        })
        return 3

    if behaviour == "escape_workspace":
        (workspace.parent / "outside.txt").write_text("secret", encoding="utf-8")
        write(result_path, {
            "schema_version": 3, "status": "succeeded", "exit_code": 0,
            "started_at": "t0", "ended_at": "t1",
            "artifacts": [{"kind": "report", "path": "../outside.txt"}],
        })
        return 0

    if behaviour == "missing_artifact":
        write(result_path, {
            "schema_version": 3, "status": "succeeded", "exit_code": 0,
            "started_at": "t0", "ended_at": "t1",
            "artifacts": [{"kind": "report", "path": "never-written.json"}],
        })
        return 0

    if behaviour == "disallowed_kind":
        (workspace / "report.json").write_text("{}", encoding="utf-8")
        write(result_path, {
            "schema_version": 3, "status": "succeeded", "exit_code": 0,
            "started_at": "t0", "ended_at": "t1",
            "artifacts": [{"kind": "not-in-the-manifest", "path": "report.json"}],
        })
        return 0

    if behaviour == "forge_authority":
        (workspace / "report.json").write_text("{}", encoding="utf-8")
        write(result_path, {
            "schema_version": 3, "status": "succeeded", "exit_code": 0,
            "started_at": "t0", "ended_at": "t1",
            "artifacts": [{"kind": "report", "path": "report.json",
                           "metadata": {"official_qor": 1.0}}],
        })
        return 0

    if behaviour == "fail":
        # No ``retryable`` flag: a failure the plugin does not ask to repeat.
        write(result_path, {
            "schema_version": 3, "status": "failed", "exit_code": 2,
            "started_at": "t0", "ended_at": "t1",
            "failure": {"category": "tool_error", "message": "the tool said no"},
        })
        return 2

    if behaviour == "fail_retryable":
        # The plugin asks for another attempt.  It is the only party that knows
        # whether trying again could help; the platform only holds the budget.
        write(result_path, {
            "schema_version": 3, "status": "failed", "exit_code": 2,
            "started_at": "t0", "ended_at": "t1",
            "failure": {"category": "transient_error", "retryable": True,
                        "message": "the tool was busy"},
        })
        return 2

    if behaviour == "noisy":
        # Genuinely endless, like a tool that logs a progress bar forever.
        # A bounded burst would finish before the deadline and prove nothing.
        import time as _time
        while True:
            print("a very chatty tool line " * 4, flush=True)
            _time.sleep(0.001)

    print(f"unknown behaviour {behaviour!r}", file=sys.stderr)
    return 9


if __name__ == "__main__":
    raise SystemExit(main())
