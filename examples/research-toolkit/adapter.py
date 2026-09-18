"""Small Toolkit example for agent-authored scripts and source patches."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_result(
    path: Path, *, status: str, exit_code: int, failure: dict | None = None
) -> None:
    result = {
        "schema_version": 3,
        "status": status,
        "exit_code": exit_code,
        "started_at": now(),
        "ended_at": now(),
        "artifacts": (
            [{"kind": "report", "path": "report.json"}] if status == "succeeded" else []
        ),
    }
    if status == "succeeded":
        report = json.loads((path.parent / "report.json").read_text(encoding="utf-8"))
        result["metrics"] = [
            {
                "name": "result_value",
                "value": report["value"],
                "unit": "score",
                "context": {
                    "source_artifact_store_key": "report.json",
                    "parser_id": "research-example",
                    "parser_version": "1",
                },
            }
        ]
    if failure is not None:
        result["failure"] = failure
    path.write_text(json.dumps(result), encoding="utf-8")


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True, capture_output=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    request_path = Path(args.request)
    result_path = Path(args.result)
    workspace = result_path.parent
    task = json.loads(request_path.read_text(encoding="utf-8"))["task"]
    inputs = task["inputs"]
    capability = inputs["capability"]

    if capability == "script":
        task_path = workspace / "task.json"
        task_path.write_text(json.dumps(task), encoding="utf-8")
        completed = run([sys.executable, inputs["script_path"]], check=False)
        (workspace / "adapter-output.log").write_text(
            completed.stdout + completed.stderr, encoding="utf-8"
        )
        if completed.returncode:
            write_result(
                result_path,
                status="failed",
                exit_code=completed.returncode,
                failure={"category": "script_error", "message": completed.stderr},
            )
            return completed.returncode
        write_result(result_path, status="succeeded", exit_code=0)
        return 0

    if capability == "patch_benchmark":
        source = inputs["source_path"]
        patch_tool = inputs["patch_tool"]
        compiler = inputs["compiler"]
        baseline = run([compiler, source, "-o", "baseline"], check=False)
        if baseline.returncode:
            write_result(
                result_path,
                status="failed",
                exit_code=baseline.returncode,
                failure={"category": "build_error", "message": baseline.stderr},
            )
            return baseline.returncode
        baseline_run = run(["./baseline"], check=False)
        applied = run([patch_tool, "-p0", "-i", inputs["patch_path"]], check=False)
        if applied.returncode:
            write_result(
                result_path,
                status="failed",
                exit_code=applied.returncode,
                failure={"category": "patch_error", "message": applied.stderr},
            )
            return applied.returncode
        built = run([compiler, source, "-o", "candidate"], check=False)
        if built.returncode:
            write_result(
                result_path,
                status="failed",
                exit_code=built.returncode,
                failure={"category": "build_error", "message": built.stderr},
            )
            return built.returncode
        candidate_run = run(["./candidate"], check=False)
        if candidate_run.returncode:
            write_result(
                result_path,
                status="failed",
                exit_code=candidate_run.returncode,
                failure={
                    "category": "benchmark_error",
                    "message": candidate_run.stderr,
                },
            )
            return candidate_run.returncode
        report = {
            "baseline": int(baseline_run.stdout.strip()),
            "value": int(candidate_run.stdout.strip()),
        }
        (workspace / "report.json").write_text(json.dumps(report), encoding="utf-8")
        write_result(result_path, status="succeeded", exit_code=0)
        return 0

    write_result(
        result_path,
        status="failed",
        exit_code=2,
        failure={
            "category": "configuration_error",
            "message": f"unsupported capability: {capability}",
        },
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
