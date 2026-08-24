#!/usr/bin/env python3
"""Isolated RTL verification adapter: fixed Verilator + Yosys commands only."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _now(): return datetime.now(timezone.utc).isoformat()
def _sha(path: Path): return hashlib.sha256(path.read_bytes()).hexdigest()
def _write(path: Path, value: dict): path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args(); started = _now(); root = args.result.parent.resolve()
    try:
        task = json.loads(args.request.read_text(encoding="utf-8"))["task"]
        rtl = task["inputs"]["rtl"]; top = task["inputs"]["top"]
        source = Path(rtl["path"]).resolve()
        if not source.is_file() or _sha(source) != rtl["sha256"]:
            raise ValueError("RTL input is missing or its immutable hash changed")
        inputs, outputs = root / "inputs", root / "outputs"; inputs.mkdir(exist_ok=True); outputs.mkdir(exist_ok=True)
        staged = inputs / "design.sv"; shutil.copy2(source, staged)
        commands = [
            [str(Path(os.environ["RTL_VERIFY_VERILATOR"])), "--lint-only", "--sv", "--top-module", top, str(staged)],
            [str(Path(os.environ["YOSYS_BIN"])), "-Q", "-p", f"read_verilog -sv {staged}; hierarchy -top {top}; proc; check"],
        ]
        log = outputs / "verification.log"; rows = []
        with log.open("w", encoding="utf-8") as handle:
            for name, command in zip(("verilator_lint", "yosys_check"), commands):
                run = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
                handle.write(f"$ {' '.join(command)}\n{run.stdout}\n")
                rows.append({"check": name, "exit_code": run.returncode})
                if run.returncode != 0:
                    _write(args.result, {"schema_version": 1, "status": "failed", "exit_code": run.returncode or 1,
                        "started_at": started, "ended_at": _now(), "metrics": [], "artifacts": [],
                        "failure": {"category": "rtl_verification_failed", "message": f"{name} failed; see adapter.log"},
                        "provenance": {"adapter": "rtl-verify-v1", "checks": rows}})
                    return run.returncode or 1
        verified = outputs / "verified_design.sv"; shutil.copy2(staged, verified)
        report = outputs / "verification.json"; report.write_text(json.dumps({"checks": rows, "top": top, "rtl_sha256": _sha(verified)}, indent=2), encoding="utf-8")
        _write(args.result, {"schema_version": 1, "status": "succeeded", "exit_code": 0, "started_at": started, "ended_at": _now(),
            "metrics": [{"name": "rtl.verilator_lint", "value": 1, "unit": "pass"}, {"name": "rtl.yosys_check", "value": 1, "unit": "pass"}],
            "artifacts": [{"kind": "rtl", "path": "outputs/verified_design.sv"}, {"kind": "verification_report", "path": "outputs/verification.json"}, {"kind": "log", "path": "outputs/verification.log"}],
            "failure": None, "provenance": {"adapter": "rtl-verify-v1", "checks": rows, "input_sha256": rtl["sha256"]}})
        return 0
    except Exception as exc:
        _write(args.result, {"schema_version": 1, "status": "failed", "exit_code": 1, "started_at": started, "ended_at": _now(), "metrics": [], "artifacts": [], "failure": {"category": "adapter_error", "message": f"{type(exc).__name__}: {exc}"}, "provenance": {"adapter": "rtl-verify-v1"}})
        return 1

if __name__ == "__main__": raise SystemExit(main())
