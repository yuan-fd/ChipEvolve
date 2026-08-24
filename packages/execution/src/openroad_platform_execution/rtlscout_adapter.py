"""Protocol adapter that executes a pinned RTLScout checkout as a black box."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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
    RTLSCOUT_PROVIDERS,
    sha256,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fallback_yosys_cost(design: Path, top: str, metric: str) -> tuple[float | None, dict[str, Any]]:
    """Measure a candidate when the pinned RTLScout parser meets a new Yosys format.

    RTLScout 87a00e expects the old ``Number of cells`` spelling.  The pinned
    Yosys 0.63 emits ``441 cells`` instead.  This is deliberately a narrow,
    recorded compatibility projection: it does not replace simulation/lint or
    turn a failed candidate into a pass.
    """
    if metric not in {"yosys_cells", "yosys_wires"}:
        return None, {"available": False, "reason": "metric_not_supported_by_compatibility_projection"}
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", top):
        return None, {"available": False, "reason": "invalid_top_identifier"}
    yosys = shutil.which("yosys")
    if not yosys:
        return None, {"available": False, "reason": "yosys_unavailable"}
    result = subprocess.run(
        [yosys, "-p", f"read_verilog -sv {design.resolve()}; hierarchy -top {top}; synth; stat"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120, check=False,
    )
    text = result.stdout
    cells = re.findall(r"(?m)^\s*(\d+)\s+cells\s*$", text)
    wires = re.findall(r"(?m)^\s*(\d+)\s+wires\s*$", text)
    if result.returncode or not cells or not wires:
        return None, {"available": False, "reason": "unable_to_parse_yosys_stat", "exit_code": result.returncode}
    stats = {"cells": int(cells[-1]), "wires": int(wires[-1])}
    return float(stats["cells" if metric == "yosys_cells" else "wires"]), {
        "available": True, "parser": "openroad-platform-yosys-compat-v1", "metrics": stats,
    }


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


def _codex_cli_candidates(*, source: Path, python: Path, workspace: Path,
                          bench: Path, spec: dict[str, Any], testbench_sha256: str,
                          model: str, max_steps: int, cost_metric: str,
                          log_path: Path) -> dict[str, Any]:
    """Use the local Codex CLI only as RTLScout's candidate author.

    RTLScout's upstream Python ReAct client has no Codex provider.  This small
    platform-side bridge retains the important boundary: Codex may propose
    ``design.sv`` inside an isolated work directory, while the immutable
    testbench hash and RTLScout's independent Verilator/Yosys evaluator decide
    every candidate.  No Codex credential enters a TaskSpec or subprocess
    environment, because the CLI uses its own local login.
    """
    codex = shutil.which("codex")
    verilator = shutil.which("verilator")
    yosys = shutil.which("yosys")
    if not codex:
        raise ValueError("codex CLI is unavailable")
    if not verilator or not yosys:
        raise ValueError(
            "RTLScout preflight requires explicit Verilator and Yosys tools; "
            f"verilator={verilator!r}, yosys={yosys!r}"
        )
    top = str(spec["top"])
    agent_dir = workspace / "codex-agent"
    agent_dir.mkdir(parents=True, exist_ok=False)
    immutable_tb = bench / "tb.sv"
    shutil.copy2(immutable_tb, agent_dir / "tb.sv")
    (agent_dir / "specir.json").write_text(json.dumps(spec, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    evaluations: list[dict[str, Any]] = []
    best: tuple[float, Path, dict[str, Any]] | None = None
    feedback = "No candidate has been evaluated yet. Create design.sv now."
    with log_path.open("w", encoding="utf-8") as log:
        for step in range(1, max_steps + 1):
            before = sha256_text((agent_dir / "tb.sv").read_text(encoding="utf-8"))
            prompt = (
                "You are the candidate-author stage of RTLScout. Read specir.json and write ONLY a "
                "synthesizable SystemVerilog candidate named design.sv for its exact top module. "
                "Do not read, edit, rename, or delete tb.sv: it is an independent frozen oracle. "
                "Use the prior evaluator feedback below to repair or improve design.sv. Do not claim pass; "
                "the evaluator will decide. Do not create a testbench, scripts, or other files.\n\n"
                f"PRIOR_FEEDBACK:\n{feedback[:12000]}"
            )
            completed = subprocess.run(
                [codex, "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "workspace-write",
                 "--model", model, "--color", "never", "-"],
                input=prompt, cwd=agent_dir, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, timeout=600, check=False,
            )
            log.write(f"\n===== CODEX STEP {step} =====\n{completed.stdout}\n")
            log.flush()
            if sha256_text((agent_dir / "tb.sv").read_text(encoding="utf-8")) != before or before != testbench_sha256:
                raise RuntimeError("Codex changed the frozen verification oracle; candidate rejected")
            design = agent_dir / "design.sv"
            if completed.returncode != 0 or not design.is_file() or design.stat().st_size == 0:
                feedback = "Candidate author did not produce a non-empty design.sv."
                evaluations.append({"eval_index": step, "passed": False, "error": feedback})
                continue
            if design.stat().st_size > 2 * 1024 * 1024:
                feedback = "Candidate exceeds the 2 MiB RTL limit."
                evaluations.append({"eval_index": step, "passed": False, "error": feedback})
                continue
            evaluation = workspace / f"codex-eval-{step}"
            saved = workspace / f"codex-eval-{step}-result"
            evaluation.mkdir()
            shutil.copy2(design, evaluation / "design.sv")
            judge = subprocess.run(
                [str(python), str(source / "run_eval.py"), str((evaluation / "design.sv").resolve()), "--workdir", str(evaluation),
                 "--benchmark", str(bench), "--top-module", top, "--cost-metric", cost_metric,
                 "--save-to", str(saved), "--json"],
                cwd=source, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=600, check=False,
            )
            log.write(f"\n===== RTLScout JUDGE STEP {step} =====\n{judge.stdout}\n")
            log.flush()
            report_path = saved / "result.json"
            if not report_path.is_file():
                evaluator_tail = (judge.stdout or "")[-8000:]
                feedback = (
                    f"RTLScout evaluator failed (exit {judge.returncode}). "
                    "Repair design.sv using this evaluator output:\n"
                    f"{evaluator_tail}"
                )
                evaluations.append({"eval_index": step, "passed": False, "error": feedback})
                continue
            report = json.loads(report_path.read_text(encoding="utf-8"))
            passed = bool(report.get("passed")) and judge.returncode == 0
            cost = report.get("cost_value")
            compatibility_cost: dict[str, Any] | None = None
            if not isinstance(cost, (int, float)):
                cost, compatibility_cost = _fallback_yosys_cost(
                    evaluation / "design.sv", top, cost_metric
                )
            if isinstance(compatibility_cost, dict) and compatibility_cost.get("available"):
                # Preserve the measured compatibility projection in the final
                # Runtime metrics; the upstream parser's null must not hide a
                # real, hash-linked Yosys measurement from the dashboard.
                report = {**report, "metrics": {
                    **(report.get("metrics") if isinstance(report.get("metrics"), dict) else {}),
                    **(compatibility_cost.get("metrics") or {}),
                }}
            row = {"eval_index": step, "candidate": "design.sv", "passed": passed,
                   "lint_ok": report.get("correctness", {}).get("lint_ok"),
                   "sim_ok": report.get("correctness", {}).get("sim_ok"),
                   "cost": cost, "compatibility_cost": compatibility_cost, "result": report}
            evaluations.append(row)
            feedback = json.dumps({"passed": passed, "cost": cost,
                                   "correctness": report.get("correctness"),
                                   "error": report.get("error")}, ensure_ascii=False)
            if passed and isinstance(cost, (int, float)):
                candidate = workspace / f"codex-passing-{step}.sv"
                shutil.copy2(design, candidate)
                if best is None or float(cost) < best[0]:
                    best = (float(cost), candidate, report)
    if best is None:
        return {"passed": False, "evaluations": evaluations,
                "error": "No Codex candidate passed the frozen RTLScout evaluator"}
    return {"passed": True, "best_cost": best[0], "best_design": str(best[1]),
            "best_metrics": best[2].get("metrics") or {}, "evaluations": evaluations,
            "num_steps": len(evaluations), "cost_metric": cost_metric,
            "tool_paths": {"codex": codex, "verilator": verilator, "yosys": yosys}}


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
        inputs = task["inputs"]
        specir_mode = inputs.get("mode") == "specir-v2"
        benchmark = "specir-v2" if specir_mode else inputs["benchmark"]
        model = parameters["model"]
        provider, model_name = model.split(":", 1)
        if provider not in RTLSCOUT_PROVIDERS or not model_name:
            raise ValueError("unsupported provider:model")
        if benchmark.startswith("/") or ".." in benchmark or "\\" in benchmark:
            raise ValueError("benchmark must stay inside the pinned source tree")
        max_steps = int(parameters["max_steps"])
        if not 1 <= max_steps <= 100:
            raise ValueError("max_steps must be between 1 and 100")
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
        benchmark_roots: list[str] = []
        if specir_mode:
            spec = inputs.get("spec")
            verification = inputs.get("verification")
            testbench = inputs.get("testbench_source")
            expected_tb_hash = inputs.get("testbench_sha256")
            testbench_top = str((inputs.get("oracle_provenance") or {}).get("testbench_top") or "")
            if not isinstance(spec, dict) or not isinstance(verification, dict) or not isinstance(testbench, str):
                raise ValueError("SpecIR-v2 task requires spec, verification and frozen testbench")
            if sha256_text(testbench) != expected_tb_hash:
                raise ValueError("Frozen testbench hash mismatch")
            if testbench_top != "tb" or not re.search(r"\bmodule\s+tb\b", testbench):
                raise ValueError("RTLScout preflight requires the frozen oracle top module to be exactly tb")
            if not re.search(r"TB_SUMMARY\s+total=", testbench):
                raise ValueError("RTLScout preflight requires TB_SUMMARY total=N errors=M output")
            top = spec.get("top")
            ports = spec.get("ports")
            if not isinstance(top, str) or not isinstance(ports, list) or not ports:
                raise ValueError("SpecIR-v2 task has invalid top or ports")
            bench = workspace / "specir-benchmarks" / benchmark
            bench.mkdir(parents=True, exist_ok=False)
            # RTLScout's upstream prompt consumes description.txt.  Make it a
            # faithful, deterministic rendering of the approved SpecIR rather
            # than silently throwing away ports/constraints/acceptance terms.
            description = "\n".join((
                "Approved SpecIR (generate exactly this top module):",
                json.dumps(spec, ensure_ascii=False, sort_keys=True, indent=2),
                "\nFrozen verification oracle is tb.sv. Do not modify it.",
            ))
            (bench / "description.txt").write_text(description, encoding="utf-8")
            # Preserve the entire user-approved contract for upstream agents;
            # neither a prose summary nor a benchmark name is sufficient for
            # an arbitrary design specification.
            (bench / "specir.json").write_text(json.dumps(spec, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            (bench / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            (bench / "metadata.json").write_text(json.dumps({
                "name": benchmark, "module_name": top, "tb_module": "tb",
                "spec_id": spec.get("spec_id"), "verification_id": verification.get("verification_id"),
                "specir_path": "specir.json", "verification_path": "verification.json",
                "oracle_sha256": expected_tb_hash,
            }, sort_keys=True), encoding="utf-8")
            (bench / "tb.sv").write_text(testbench, encoding="utf-8")
            benchmark_roots = ["--benchmarks-root", str(bench.parent)]
        if provider == "codex-cli":
            if not specir_mode:
                raise ValueError("codex-cli is supported only for the SpecIR-v2 RTLScout entry")
            payload = _codex_cli_candidates(
                source=source, python=python, workspace=workspace, bench=bench,
                spec=spec, testbench_sha256=expected_tb_hash, model=model_name,
                max_steps=max_steps, cost_metric=str(parameters["cost_metric"]),
                log_path=upstream_log,
            )
            if payload.get("passed") is not True:
                return _failure(args.result, started, "rtl_validation_failed",
                                str(payload.get("error") or "no Codex candidate passed"))
            outputs = workspace / "outputs"; outputs.mkdir(exist_ok=True)
            rtl = outputs / "design.sv"; shutil.copy2(Path(payload["best_design"]), rtl)
            report = outputs / "rtlscout_result.json"; report.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            summary = outputs / "summary.txt"; summary.write_text(
                f"RTLScout Codex bridge: PASS; {payload['best_cost']} {payload['cost_metric']}\n",
                encoding="utf-8")
            metrics = [{"name": f"rtlscout.{name}", "value": value,
                        "unit": "count" if name.startswith("num_") or name == "transistors" else None,
                        "parser_id": "rtlscout-result", "parser_version": "1",
                        "context": {"benchmark": benchmark, "provider": provider, "input_mode": "specir-v2"}}
                       for name, value in (payload.get("best_metrics") or {}).items()
                       if isinstance(value, (int, float, str))]
            _write(args.result, {
                "schema_version": 1, "status": "succeeded", "exit_code": 0,
                "started_at": started, "ended_at": _now(), "metrics": metrics,
                "artifacts": [{"kind": "rtl", "path": "outputs/design.sv", "language": "systemverilog"},
                              {"kind": "rtlscout_result", "path": "outputs/rtlscout_result.json"},
                              {"kind": "report", "path": "outputs/summary.txt"},
                              {"kind": "log", "path": "rtlscout.log"}], "failure": None,
                "provenance": {"adapter": "rtlscout-v2-codex-bridge", "upstream_commit": actual_commit,
                               "provider": provider, "model": model_name, "fake_model": False,
                               "rtl_sha256": sha256(rtl), "input_mode": "specir-v2",
                               "spec_id": spec.get("spec_id"), "oracle_immutable": True,
                               "candidate_evaluations": len(payload.get("evaluations") or []),
                               "tool_paths": payload.get("tool_paths")},
            })
            return 0
        command = [
            str(python), str(source / "run_benchmark.py"),
            "--benchmark", benchmark,
            "--model", model,
            "--runs-dir", str(runs),
            "--max-steps", str(max_steps),
            "--cost-metric", str(parameters["cost_metric"]),
            "--dont-save-workspaces",
            *benchmark_roots,
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
                    "context": {"benchmark": benchmark, "provider": provider,
                                "input_mode": "specir-v2" if specir_mode else "benchmark"},
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
                "adapter": "rtlscout-v2" if specir_mode else "rtlscout-v1",
                "upstream_commit": actual_commit,
                "provider": provider,
                "model": model_name,
                "fake_model": provider == "fake",
                "rtl_sha256": sha256(rtl),
                "input_mode": "specir-v2" if specir_mode else "benchmark",
                "spec_id": (inputs.get("spec") or {}).get("spec_id") if specir_mode else None,
            },
        })
        return 0
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError,
            subprocess.SubprocessError) as exc:
        return _failure(args.result, started, "adapter_error", f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
