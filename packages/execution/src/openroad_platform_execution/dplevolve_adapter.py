#!/usr/bin/env python3
"""Run the fixed DPLEvolve control-repository gate without source mutation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    started = _now()
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        task = request["task"]
        expected_parameters = {
            "mode": "release-readiness-static",
            "skip_teacher_dry_run": True,
            "allow_eda_execution": False,
            "allow_source_mutation": False,
        }
        if task.get("parameters") != expected_parameters:
            return _fail(args.result, started, "mode_not_allowed",
                         "DPLEvolve plugin permits only the read-only static release gate")
        source = Path(os.environ["DPLEVOLVE_SOURCE"]).resolve()
        before, count = _source_tree_digest(source)
        expected = os.environ["DPLEVOLVE_EXPECTED_TREE_SHA256"]
        expected_count = int(os.environ["DPLEVOLVE_EXPECTED_FILE_COUNT"])
        if before != expected or count != expected_count:
            return _fail(args.result, started, "source_mismatch",
                         "DPLEvolve fixed source content changed before audit")
        workspace = args.result.parent.resolve()
        state = workspace / "dplevolve-state"
        state.mkdir(parents=True, exist_ok=True)
        staged_source = workspace / "control-repository"
        shutil.copytree(
            source, staged_source,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
        )
        subprocess.run(["git", "init", "-q", str(staged_source)], check=True)
        subprocess.run(["git", "-C", str(staged_source), "add", "-f", "."], check=True)
        log_path = workspace / "release_readiness.log"
        command = [
            "bash", str(staged_source / "scripts/repo/check_release_readiness.sh"),
            "--skip-teacher-dry-run",
        ]
        environment = os.environ.copy()
        environment.update({
            "DPL_EVOLVE_AGENT_ROOT": str(staged_source),
            "DPL_EVOLVE_STATE_ROOT": str(state),
            "DPL_EVOLVE_PYTHON": os.environ["DPLEVOLVE_PYTHON"],
            "PYTHONPYCACHEPREFIX": str(state / "pycache"),
        })
        with log_path.open("w", encoding="utf-8") as stream:
            completed = subprocess.run(
                command, cwd=staged_source, env=environment,
                stdout=stream, stderr=subprocess.STDOUT, text=True,
            )
        after, after_count = _source_tree_digest(source)
        if before != after or count != after_count:
            return _fail(args.result, started, "source_mutation",
                         "DPLEvolve release gate modified protected source")
        if completed.returncode:
            return _fail(args.result, started, "release_gate_failed",
                         "DPLEvolve release-readiness gate returned non-zero",
                         completed.returncode)
        lock_path = workspace / "source_lock.json"
        lock = {
            "schema_version": 1,
            "repository": "CODA-Team/DPLEvolve",
            "commit": os.environ["DPLEVOLVE_EXPECTED_COMMIT"],
            "tree_sha256": before,
            "file_count": count,
            "license": os.environ["DPLEVOLVE_LICENSE"],
            "audit_mode": "release-readiness-static",
            "source_mutated": False,
            "staged_mirror_used": True,
        }
        _write(lock_path, lock)
        report_path = workspace / "audit_report.json"
        report = {
            "schema_version": 1,
            "release_gate": "passed",
            "teacher_dry_run_skipped": True,
            "eda_executed": False,
            "source_mutation_allowed": False,
            "candidate_promotion_applied": False,
            "runtime_is_status_authority": True,
            "command": ["scripts/repo/check_release_readiness.sh",
                        "--skip-teacher-dry-run"],
        }
        _write(report_path, report)
        _write(args.result, {
            "schema_version": 1,
            "status": "succeeded",
            "exit_code": 0,
            "started_at": started,
            "ended_at": _now(),
            "metrics": [
                {"name": "dplevolve.release_gate_passed", "value": 1,
                 "unit": "boolean", "source": "observed"},
                {"name": "dplevolve.source_file_count", "value": count,
                 "unit": "count", "source": "observed"},
            ],
            "artifacts": [
                {"kind": "release_gate_log", "path": log_path.name},
                {"kind": "source_lock", "path": lock_path.name},
                {"kind": "audit_report", "path": report_path.name},
            ],
            "failure": None,
            "provenance": lock,
        })
        return 0
    except Exception as exc:
        return _fail(args.result, started, "adapter_error", f"{type(exc).__name__}: {exc}")


def _source_tree_digest(source: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    for path in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(source)
        if any(part in {".git", "__pycache__"} for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"source symlink is forbidden: {relative}")
        if not path.is_file() or path.suffix == ".pyc":
            continue
        payload = path.read_bytes()
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(payload)).encode("ascii"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(payload).hexdigest().encode("ascii"))
        digest.update(b"\n")
        count += 1
    return digest.hexdigest(), count


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def _fail(path: Path, started: str, category: str, message: str,
          code: int = 1) -> int:
    _write(path, {
        "schema_version": 1, "status": "failed", "exit_code": code,
        "started_at": started, "ended_at": _now(), "metrics": [],
        "artifacts": [], "failure": {"category": category, "message": message},
        "provenance": {"source_mutation_allowed": False},
    })
    return code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
