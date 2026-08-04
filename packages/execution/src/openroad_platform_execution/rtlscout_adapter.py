"""Protocol adapter that executes a pinned RTLScout checkout as a black box."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
for source_root in (
    REPOSITORY_ROOT / "packages/contracts/src",
    REPOSITORY_ROOT / "packages/execution/src",
):
    sys.path.insert(0, str(source_root))

from openroad_platform_execution.rtlscout_plugin import (  # noqa: E402
    RTLSCOUT_CREDENTIALS,
    RTLSCOUT_PROVIDERS,
    sha256,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _failure(result: Path, started: str, category: str, message: str, code: int = 1) -> int:
    _write(result, {
        "schema_version": 1,
        "status": "failed",
        "exit_code": code,
        "started_at": started,
        "ended_at": _now(),
        "metrics": [],
        "artifacts": [],
        "failure": {"category": category, "message": message},
        "provenance": {"adapter": "rtlscout-v1"},
    })
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args(argv)
    started = _now()
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        task = request["task"]
        parameters = task["parameters"]
        benchmark = task["inputs"]["benchmark"]
        model = parameters["model"]
        provider, model_name = model.split(":", 1)
        if provider not in RTLSCOUT_PROVIDERS or not model_name:
            raise ValueError("unsupported provider:model")
        if benchmark.startswith("/") or ".." in benchmark or "\\" in benchmark:
            raise ValueError("benchmark must stay inside the pinned source tree")
        max_steps = int(parameters["max_steps"])
        if not 1 <= max_steps <= 100:
            raise ValueError("max_steps must be between 1 and 100")
        credential_name = RTLSCOUT_CREDENTIALS.get(provider)
        if credential_name and not os.environ.get(credential_name):
            return _failure(
                args.result, started, "credential_unavailable",
                f"{provider} execution requires {credential_name} in the adapter environment",
            )

        source = Path(os.environ["RTLSCOUT_SOURCE"]).resolve()
        python = Path(os.environ["RTLSCOUT_PYTHON"]).absolute()
        expected_commit = os.environ["RTLSCOUT_EXPECTED_COMMIT"]
        if not python.is_file() or not (source / "run_benchmark.py").is_file():
            raise ValueError("pinned RTLScout source or Python executable is missing")
        actual_commit = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout.strip()
        if actual_commit != expected_commit:
            raise ValueError("pinned RTLScout source commit changed")

        workspace = args.result.parent.resolve()
        runs = workspace / "rtlscout-runs"
        upstream_log = workspace / "rtlscout.log"
        command = [
            str(python), str(source / "run_benchmark.py"),
            "--benchmark", benchmark,
            "--model", model,
            "--runs-dir", str(runs),
            "--max-steps", str(max_steps),
            "--cost-metric", str(parameters["cost_metric"]),
            "--dont-save-workspaces",
        ]
        with upstream_log.open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                command, cwd=source, env=dict(os.environ),
                stdout=log, stderr=subprocess.STDOUT, text=True, check=False,
            )
        if completed.returncode != 0:
            return _failure(
                args.result, started, "upstream_error",
                f"RTLScout exited with code {completed.returncode}; see rtlscout.log",
                completed.returncode,
            )
        candidates = sorted(runs.rglob("result.json"), key=lambda item: item.stat().st_mtime)
        if not candidates:
            return _failure(args.result, started, "upstream_error", "RTLScout produced no result.json")
        upstream_result = candidates[-1]
        payload = json.loads(upstream_result.read_text(encoding="utf-8"))
        if payload.get("workdir"):
            resolved = Path(payload["workdir"]).resolve()
            try:
                resolved.relative_to(runs.resolve())
            except ValueError as exc:
                raise ValueError("RTLScout result workdir escaped the attempt workspace") from exc
        if payload.get("passed") is not True:
            return _failure(
                args.result, started, "rtl_validation_failed",
                f"RTLScout completed but no fully correct design passed: {payload.get('error') or 'unknown'}",
            )

        run_root = upstream_result.parent
        best = run_root / "best_design" / "design.sv"
        if not best.is_file() or best.stat().st_size == 0:
            raise ValueError("passing RTLScout result has no best_design/design.sv")
        outputs = workspace / "outputs"
        outputs.mkdir(exist_ok=True)
        rtl = outputs / "design.sv"
        report = outputs / "rtlscout_result.json"
        summary = outputs / "summary.txt"
        shutil.copy2(best, rtl)
        shutil.copy2(upstream_result, report)
        source_summary = run_root / "summary.txt"
        if source_summary.is_file() and source_summary.stat().st_size:
            shutil.copy2(source_summary, summary)
        else:
            summary.write_text(
                f"RTLScout {benchmark}: PASS; {payload.get('best_cost')} {payload.get('cost_metric')}\n",
                encoding="utf-8",
            )
        metrics = []
        for name, value in (payload.get("best_metrics") or {}).items():
            if isinstance(value, (int, float, str)):
                metrics.append({
                    "name": f"rtlscout.{name}", "value": value,
                    "unit": "count" if name.startswith("num_") or name == "transistors" else None,
                    "parser_id": "rtlscout-result", "parser_version": "1",
                    "context": {"benchmark": benchmark, "provider": provider},
                })
        _write(args.result, {
            "schema_version": 1,
            "status": "succeeded",
            "exit_code": 0,
            "started_at": started,
            "ended_at": _now(),
            "metrics": metrics,
            "artifacts": [
                {"kind": "rtl", "path": "outputs/design.sv", "language": "systemverilog"},
                {"kind": "rtlscout_result", "path": "outputs/rtlscout_result.json"},
                {"kind": "report", "path": "outputs/summary.txt"},
                {"kind": "log", "path": "rtlscout.log"},
            ],
            "failure": None,
            "provenance": {
                "adapter": "rtlscout-v1",
                "upstream_commit": actual_commit,
                "provider": provider,
                "model": model_name,
                "fake_model": provider == "fake",
                "rtl_sha256": sha256(rtl),
            },
        })
        return 0
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError,
            subprocess.SubprocessError) as exc:
        return _failure(args.result, started, "adapter_error", f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
