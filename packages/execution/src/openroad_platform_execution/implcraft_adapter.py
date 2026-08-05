#!/usr/bin/env python3
"""Run pinned EDACraft ImplCraft in truthful script-generation mode."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    started = _now()
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        task = request["task"]
        if task["parameters"].get("mode") != "dry-run":
            return _fail(args.result, started, "mode_not_allowed",
                         "ImplCraft v1 only permits truthful dry-run script generation")
        source = Path(os.environ["EDACRAFT_ROOT"]).resolve()
        if _git(source) != os.environ["EDACRAFT_EXPECTED_COMMIT"]:
            return _fail(args.result, started, "source_mismatch",
                         "EDACraft fixed commit changed")
        workspace = args.result.parent.resolve()
        staged_rtl = _stage_rtl(task, workspace)
        config_path = workspace / "implcraft_project.yaml"
        work_root = workspace / "implcraft-work"
        config_path.write_text(
            yaml.safe_dump(_config(task, staged_rtl, work_root), sort_keys=False),
            encoding="utf-8",
        )
        snapshot_path = workspace / "toolchain_snapshot.json"
        snapshot = {
            "schema_version": 1,
            "edacraft_commit": os.environ["EDACRAFT_EXPECTED_COMMIT"],
            "component": "ImplCraft",
            "component_version": "0.2.0",
            "python": _version([os.environ["IMPLCRAFT_PYTHON"], "-V"]),
            "execution_class": "script-generation-only",
            "commercial_eda_executed": False,
            "commercial_tools_available": {
                name: shutil.which(name) is not None
                for name in (
                    "dc_shell", "icc2_shell", "pt_shell", "calibre",
                    "innovus", "tempus", "pegasus",
                )
            },
            "license": "custom MIT-like non-commercial",
        }
        snapshot_path.write_text(
            json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        log_path = workspace / "implcraft.log"
        command = [
            os.environ["IMPLCRAFT_PYTHON"], "-m", "src.run_flow",
            "--config", str(config_path), "--work-root", str(work_root),
            "--dry-run", "--stop-at", task["parameters"]["stop_at"],
        ]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.environ["IMPLCRAFT_SOURCE"]
        with log_path.open("w", encoding="utf-8") as stream:
            completed = subprocess.run(
                command, cwd=os.environ["IMPLCRAFT_SOURCE"], env=environment,
                stdout=stream, stderr=subprocess.STDOUT, text=True,
            )
        if not log_path.stat().st_size:
            log_path.write_text(
                f"ImplCraft dry-run exit_code={completed.returncode} "
                f"stop_at={task['parameters']['stop_at']}\n",
                encoding="utf-8",
            )
        if completed.returncode:
            return _fail(args.result, started, "upstream_failure",
                         "ImplCraft dry-run returned non-zero", completed.returncode)
        state_path = work_root / "design_state.json"
        report_path = work_root / "qor_report.txt"
        scripts = sorted(work_root.glob("**/*.tcl"))
        required = [state_path, report_path, *scripts]
        if not scripts or any(not path.is_file() or path.stat().st_size == 0 for path in required):
            return _fail(args.result, started, "artifact_missing",
                         "ImplCraft did not produce state, report, and Tcl scripts")
        artifacts = [
            {"kind": "implcraft_config", "path": config_path.name},
            {"kind": "implcraft_state", "path": str(state_path.relative_to(workspace))},
            {"kind": "report", "path": str(report_path.relative_to(workspace))},
            {"kind": "toolchain_snapshot", "path": snapshot_path.name},
            {"kind": "log", "path": log_path.name},
        ]
        artifacts.extend(
            {"kind": "eda_script", "path": str(path.relative_to(workspace)),
             "stage": path.parent.parent.name, "tool": path.parent.name}
            for path in scripts
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        completed_stages = sum(
            value.get("status") == "passed"
            for value in state.get("stage_results", {}).values()
            if isinstance(value, dict)
        )
        result = {
            "schema_version": 1, "status": "succeeded", "exit_code": 0,
            "started_at": started, "ended_at": _now(), "artifacts": artifacts,
            "metrics": [
                {"name": "implcraft.generated_scripts", "value": len(scripts),
                 "unit": "count", "context": {"mode": "dry-run"}},
                {"name": "implcraft.completed_stages", "value": completed_stages,
                 "unit": "count", "context": {"mode": "dry-run"}},
            ],
            "failure": None,
            "provenance": {
                **snapshot,
                "rtl_sha256": task["inputs"]["rtl"]["sha256"],
                "stop_at": task["parameters"]["stop_at"],
            },
        }
        _write(args.result, result)
        return 0
    except Exception as exc:
        return _fail(args.result, started, "adapter_error",
                     f"{type(exc).__name__}: {exc}")


def _config(task: dict, rtl: Path, work_root: Path) -> dict:
    period = float(task["parameters"]["clock_period_ns"])
    return {
        "design": {
            "name": task["design_id"], "top_module": task["inputs"]["top"],
            "clock_period_ns": period, "clock_name": task["inputs"]["clock"],
            "target_utilization": 0.65,
        },
        "pdk": {
            "name": "platform-placeholder", "tech_file": "/pdk/tech.tf",
            "metal_stack": ["M1", "M2", "M3", "M4", "M5", "M6"],
        },
        "libraries": {
            "std_cell_libs": ["/pdk/stdcell.db"],
            "ndm_libs": ["/pdk/stdcell.ndm"],
        },
        "clocks": [{"name": task["inputs"]["clock"], "period_ns": period,
                    "pin_or_port": task["inputs"]["clock"]}],
        "rtl": {"files": [str(rtl)]},
        "flow": {
            "work_root": str(work_root), "dry_run": True,
            "tool_chain": task["parameters"]["tool_chain"],
        },
    }


def _stage_rtl(task: dict, workspace: Path) -> Path:
    reference = task["inputs"]["rtl"]
    source = Path(reference["path"]).expanduser().resolve()
    if (not source.is_file() or source.stat().st_size != reference["size_bytes"]
            or _sha256(source) != reference["sha256"]):
        raise ValueError("ImplCraft RTL size/SHA-256 mismatch")
    destination = workspace / "inputs/design.v"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    if _sha256(destination) != reference["sha256"]:
        raise ValueError("Staged ImplCraft RTL SHA-256 mismatch")
    return destination


def _git(root: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()


def _version(command: list[str]) -> str:
    completed = subprocess.run(
        command, check=False, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True,
    )
    return completed.stdout.strip().splitlines()[0]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def _fail(path: Path, started: str, category: str, message: str,
          code: int = 1) -> int:
    _write(path, {
        "schema_version": 1, "status": "failed", "exit_code": code,
        "started_at": started, "ended_at": _now(), "metrics": [],
        "artifacts": [], "failure": {"category": category, "message": message},
        "provenance": {},
    })
    return code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
