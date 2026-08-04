"""Workspace-isolated protocol adapter for the official TaiWei gcd ORD flow."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
for source_root in (REPOSITORY_ROOT / "packages/contracts/src",
                    REPOSITORY_ROOT / "packages/execution/src"):
    sys.path.insert(0, str(source_root))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    started = _now()
    log = args.result.parent / "taiwei.log"
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        task = request["task"]
        if task["inputs"] != {"flow": "ord", "tech": "asap7_3D", "case": "gcd"}:
            return _fail(args.result, started, "policy_rejected", "Only ord/asap7_3D/gcd is allowed")
        source = Path(os.environ["TAIWEI_SOURCE"]).resolve()
        staged = args.result.parent / "taiwei-source"
        _archive(source, staged)
        snapshot = {
            "taiwei_commit": os.environ["TAIWEI_EXPECTED_COMMIT"],
            "orfs_root": os.environ["TAIWEI_ORFS_ROOT"],
            "orfs_commit": os.environ["TAIWEI_ORFS_COMMIT"],
            "openroad_commit": os.environ["TAIWEI_OPENROAD_COMMIT"],
            "openroad_bin": os.environ["OPENROAD_EXE"],
            "yosys_bin": os.environ["YOSYS_EXE"],
        }
        snapshot_path = args.result.parent / "toolchain_snapshot.json"
        _write(snapshot_path, snapshot)
        env = os.environ.copy()
        env.update({"ORFS_DIR": os.environ["TAIWEI_ORFS_ROOT"],
                    "FLOW_HOME": str(staged), "WORK_DIR": str(staged)})
        command = [sys.executable, "run_experiments.py", "--flow", "ord",
                   "--tech", "asap7_3D", "--case", "gcd"]
        with log.open("w", encoding="utf-8") as stream:
            completed = subprocess.run(command, cwd=staged, env=env,
                                       stdout=stream, stderr=subprocess.STDOUT, text=True)
        if log.stat().st_size == 0:
            log.write_text("TaiWei command completed without console output.\n", encoding="utf-8")
        if completed.returncode:
            return _fail(args.result, started, "upstream_failure",
                         "TaiWei gcd flow returned non-zero", completed.returncode)
        artifacts = _discover(args.result.parent, staged)
        required = {"three_d_eval", "three_d_summary", "gds"}
        missing = required - {item["kind"] for item in artifacts}
        if missing:
            return _fail(args.result, started, "artifact_missing",
                         f"TaiWei outputs missing: {sorted(missing)}")
        artifacts.extend([{"kind": "toolchain_snapshot", "path": snapshot_path.name},
                          {"kind": "log", "path": log.name}])
        _write(args.result, {"schema_version": 1, "status": "succeeded", "exit_code": 0,
                            "started_at": started, "ended_at": _now(), "metrics": [],
                            "artifacts": artifacts, "failure": None,
                            "provenance": {**snapshot, "real_3d": True}})
        return 0
    except Exception as exc:
        return _fail(args.result, started, "adapter_error", f"{type(exc).__name__}: {exc}")


def _archive(source: Path, destination: Path) -> None:
    destination.mkdir()
    archive = subprocess.Popen(["git", "-C", str(source), "archive", "HEAD"],
                               stdout=subprocess.PIPE)
    extract = subprocess.run(["tar", "-x", "-C", str(destination)],
                             stdin=archive.stdout, check=False)
    if archive.stdout:
        archive.stdout.close()
    code = archive.wait()
    if code or extract.returncode:
        raise RuntimeError("Cannot stage immutable TaiWei source snapshot")


def _discover(workspace: Path, staged: Path) -> list[dict]:
    specs = (("three_d_eval", "**/openroad_eval.json"),
             ("three_d_summary", "**/final_summary.txt"), ("gds", "**/*.gds"),
             ("three_d_view", "**/*3d*.png"))
    artifacts = []
    for kind, pattern in specs:
        for path in sorted(staged.glob(pattern)):
            if path.is_file() and path.stat().st_size:
                artifacts.append({"kind": kind, "path": str(path.relative_to(workspace))})
    return artifacts


def _fail(path: Path, started: str, category: str, message: str, code: int = 1) -> int:
    _write(path, {"schema_version": 1, "status": "failed", "exit_code": code,
                  "started_at": started, "ended_at": _now(), "metrics": [],
                  "artifacts": [], "failure": {"category": category, "message": message},
                  "provenance": {"real_3d": False}})
    return code


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
