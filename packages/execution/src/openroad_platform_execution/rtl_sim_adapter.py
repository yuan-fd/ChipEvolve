#!/usr/bin/env python3
"""Fixed Icarus simulation adapter for frozen RTL and testbench inputs."""
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
    parser = argparse.ArgumentParser(); parser.add_argument("--request", type=Path, required=True); parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args(); started = _now(); root = args.result.parent.resolve()
    try:
        task = json.loads(args.request.read_text(encoding="utf-8"))["task"]
        inputs = task["inputs"]
        for name in ("rtl", "testbench"):
            item, path = inputs[name], Path(inputs[name]["path"]).resolve()
            if not path.is_file() or _sha(path) != item["sha256"]:
                raise ValueError(f"{name} input is missing or immutable hash changed")
        staged, outputs = root / "inputs", root / "outputs"; staged.mkdir(exist_ok=True); outputs.mkdir(exist_ok=True)
        rtl, tb = staged / "design.sv", staged / "frozen_tb.sv"; shutil.copy2(inputs["rtl"]["path"], rtl); shutil.copy2(inputs["testbench"]["path"], tb)
        image, log = outputs / "simulation.out", outputs / "simulation.log"
        commands = [[os.environ["IVERILOG_BIN"], "-g2012", "-s", inputs["top"], "-o", str(image), str(rtl), str(tb)], [os.environ["VVP_BIN"], str(image)]]
        rows = []
        with log.open("w", encoding="utf-8") as handle:
            for name, command in zip(("compile", "simulate"), commands):
                run = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
                handle.write(f"$ {' '.join(command)}\n{run.stdout}\n"); rows.append({"check": name, "exit_code": run.returncode})
                if run.returncode:
                    _write(args.result, {"schema_version": 1, "status": "failed", "exit_code": run.returncode, "started_at": started, "ended_at": _now(), "metrics": [], "artifacts": [], "failure": {"category": "rtl_simulation_failed", "message": f"{name} failed; see log"}, "provenance": {"adapter": "rtl-sim-v1", "checks": rows}}); return run.returncode
        report = outputs / "simulation.json"; report.write_text(json.dumps({"top": inputs["top"], "checks": rows, "rtl_sha256": _sha(rtl), "testbench_sha256": _sha(tb)}, indent=2), encoding="utf-8")
        _write(args.result, {"schema_version": 1, "status": "succeeded", "exit_code": 0, "started_at": started, "ended_at": _now(), "metrics": [{"name": "rtl.simulation", "value": 1, "unit": "pass"}], "artifacts": [{"kind": "simulation_report", "path": "outputs/simulation.json"}, {"kind": "log", "path": "outputs/simulation.log"}], "failure": None, "provenance": {"adapter": "rtl-sim-v1", "checks": rows, "input_sha256": {"rtl": inputs["rtl"]["sha256"], "testbench": inputs["testbench"]["sha256"]}}})
        return 0
    except Exception as exc:
        _write(args.result, {"schema_version": 1, "status": "failed", "exit_code": 1, "started_at": started, "ended_at": _now(), "metrics": [], "artifacts": [], "failure": {"category": "adapter_error", "message": f"{type(exc).__name__}: {exc}"}, "provenance": {"adapter": "rtl-sim-v1"}}); return 1
if __name__ == "__main__": raise SystemExit(main())
