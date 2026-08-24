#!/usr/bin/env python3
"""Run a frozen KPI-only versus typed-EDAIR factual QA experiment."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUTS = {
    "gcd": ROOT / "artifacts/v2-real-bo-suite-seed20260826/gcd/edair-7aa3a35ec2ed41e0a0d469b83aa25b5d.json",
    "fifo": ROOT / "artifacts/v2-real-bo-suite-seed20260826/fifo",
    "uart_tx": ROOT / "artifacts/v2-real-bo-suite-seed20260826/uart_tx",
    "ibex_alu": ROOT / "artifacts/v2-real-bo-suite-seed20260826/ibex_alu",
}


def _resolve_input(value: Path) -> Path:
    if value.is_file():
        return value
    candidates = sorted(value.glob("edair-*.json"))
    if not candidates:
        raise FileNotFoundError(f"no EDAIR export under {value}")
    return candidates[0]


def build_questions(export: dict) -> list[dict]:
    edair = export["edair"]; run = edair["run"]; physical_report = run["physical_report"]
    kpi = physical_report["kpi"]
    stage = next(item for item in physical_report["stages"] if item["stage"] == "place")
    paths = (edair.get("timing") or {}).get("paths") or []
    worst = min(paths, key=lambda item: float(item["slack_ns"]))
    nets = (edair.get("physical") or {}).get("nets") or []
    max_net = sorted(nets, key=lambda item: (-int(item.get("fanout") or 0), str(item["name"])))[0]
    fidelity = (edair.get("timing") or {}).get("parser_fidelity") or {}
    return [
        {"id": "q01", "question": "finish setup WNS (ns)?", "answer": kpi["setup_wns_ns"]},
        {"id": "q02", "question": "place-stage setup WNS (ns)?", "answer": stage["metrics"]["setup_wns_ns"]},
        {"id": "q03", "question": "logical instance object count?", "answer": len(edair["design"]["instances"])},
        {"id": "q04", "question": "physical placed instance object count?", "answer": len(edair["physical"]["instances"])},
        {"id": "q05", "question": "parsed timing path count?", "answer": len(paths)},
        {"id": "q06", "question": "minimum parsed timing slack (ns)?", "answer": worst["slack_ns"]},
        {"id": "q07", "question": "endpoint of the minimum-slack parsed path?", "answer": worst["endpoint"]},
        {"id": "q08", "question": "registered raw artifact count?", "answer": len(edair["raw_artifacts"])},
        {"id": "q09", "question": "top-level logical port count?", "answer": len(edair["design"]["ports"])},
        {"id": "q10", "question": "maximum recovered logical-net fanout?", "answer": max_net["fanout"]},
        {"id": "q11", "question": "lexicographically first net having maximum recovered fanout?", "answer": max_net["name"]},
        {"id": "q12", "question": "unparsed timing report block count?", "answer": fidelity["unparsed_blocks"]},
    ]


def kpi_context(export: dict) -> dict:
    report = export["edair"]["run"]["physical_report"]
    return {"schema_version": 1, "kind": "kpi_only_context", "kpi": report["kpi"],
            "notice": "No stage, graph, timing-path, placement, artifact-directory or fidelity objects are included."}


def judge(expected: object, observed: object) -> bool:
    if isinstance(expected, bool):
        return observed is expected
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if not isinstance(observed, (int, float)) or isinstance(observed, bool):
            return False
        return math.isclose(float(expected), float(observed), rel_tol=1e-6, abs_tol=1e-6)
    return str(observed) == str(expected)


def _extract_json(text: str) -> dict:
    for start in [index for index, char in enumerate(text) if char == "{"]:
        try:
            value, _ = json.JSONDecoder().raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("answers"), dict):
            return value
    raise ValueError("model output has no answers JSON object")


def _write(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-calls", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.max_calls <= 4:
        raise SystemExit("max-calls must be 1-4")
    codex = shutil.which("codex")
    if not codex:
        raise SystemExit("codex CLI unavailable")
    output = args.output.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit("output must be a new or empty directory")
    output.mkdir(parents=True, exist_ok=True)
    protocol_path = ROOT / "experiments/v2-paper-20260825/edair-protocol.json"
    protocol_bytes = protocol_path.read_bytes(); protocol = json.loads(protocol_bytes)
    (output / "protocol.snapshot.json").write_bytes(protocol_bytes)
    datasets = {}
    for design in protocol["designs"]:
        path = _resolve_input(DEFAULT_INPUTS[design])
        export = json.loads(path.read_text(encoding="utf-8"))
        datasets[design] = {"path": path, "export": export,
                            "questions": build_questions(export)}
    tasks = [(design, arm, repetition) for design in protocol["designs"]
             for arm in protocol["arms"] for repetition in range(1, protocol["repetitions"] + 1)]

    def run(item: tuple[str, str, int]) -> dict:
        design, arm, repetition = item; dataset = datasets[design]
        context = kpi_context(dataset["export"]) if arm == "kpi_only" else dataset["export"]["edair"]
        workspace = output / "workspaces" / design / arm / f"rep-{repetition:02d}"
        workspace.mkdir(parents=True, exist_ok=False)
        (workspace / "context.json").write_text(json.dumps(context, ensure_ascii=False), encoding="utf-8")
        questions = [{"id": q["id"], "question": q["question"]} for q in dataset["questions"]]
        (workspace / "questions.json").write_text(json.dumps(questions, ensure_ascii=False, indent=2), encoding="utf-8")
        prompt = ("Use local read-only file tools or read-only shell commands to read context.json "
                  "and questions.json, and do not access any other file or the network. "
                  "Answer every question ID listed in questions.json using only context.json. "
                  "If the requested fact is absent, answer the exact string UNKNOWN; do not guess. "
                  "Return one JSON object exactly shaped as {\"answers\":{\"q01\":value,...}}. "
                  "The answers object must contain all 12 IDs q01 through q12 exactly once.")
        started = time.monotonic()
        process = subprocess.Popen(
            [codex, "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
             "--model", "gpt-5.6-terra", "--color", "never", "-"],
            cwd=workspace, text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, start_new_session=True)
        try:
            raw, _ = process.communicate(prompt, timeout=900)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            raise
        completed_returncode = process.returncode
        raw = raw or ""; (workspace / "model-output.txt").write_text(raw, encoding="utf-8")
        try:
            answers = _extract_json(raw)["answers"] if completed_returncode == 0 else {}
            parse_error = None
        except ValueError as exc:
            answers = {}; parse_error = str(exc)
        judged = []
        for question in dataset["questions"]:
            observed = answers.get(question["id"], "__MISSING__")
            unknown = observed == "UNKNOWN"
            judged.append({"id": question["id"], "expected": question["answer"],
                           "observed": observed, "correct": judge(question["answer"], observed),
                           "unknown": unknown})
        return {"design": design, "arm": arm, "repetition": repetition,
                "returncode": completed_returncode, "parse_error": parse_error,
                "correct": sum(x["correct"] for x in judged), "total": len(judged),
                "unknown": sum(x["unknown"] for x in judged),
                "false_answers": sum(not x["correct"] and not x["unknown"] for x in judged),
                "wall_seconds": time.monotonic() - started, "output_bytes": len(raw.encode()),
                "context_bytes": (workspace / "context.json").stat().st_size,
                "source_sha256": hashlib.sha256(dataset["path"].read_bytes()).hexdigest(),
                "judgements": judged}

    rows = []; launched_at = datetime.now(timezone.utc).isoformat()
    preflight = run((protocol["designs"][0], "kpi_only", 0))
    preflight_answers = {row["id"]: row for row in preflight["judgements"]}
    preflight_ok = (preflight.get("returncode") == 0 and preflight.get("parse_error") is None
                    and len(preflight_answers) == protocol["questions_per_design"]
                    and preflight_answers["q01"]["correct"] is True
                    and preflight_answers["q02"]["unknown"] is True)
    _write(output / "harness-preflight.json", {
        "schema_version": 1, "accepted": preflight_ok, "result": preflight,
        "rule": "all question IDs returned; available KPI recovered; absent stage fact reported UNKNOWN",
    })
    if not preflight_ok:
        raise SystemExit("EDAIR QA harness preflight failed; main calls were not launched")
    with ThreadPoolExecutor(max_workers=args.max_calls) as pool:
        futures = {pool.submit(run, item): item for item in tasks}
        for future in as_completed(futures):
            design, arm, repetition = futures[future]
            try:
                rows.append(future.result())
            except Exception as exc:
                rows.append({"design": design, "arm": arm, "repetition": repetition,
                             "correct": 0, "total": protocol["questions_per_design"],
                             "unknown": 0, "false_answers": protocol["questions_per_design"],
                             "error": f"{type(exc).__name__}: {exc}"})
            _write(output / "progress.json", {"schema_version": 1,
                   "protocol_id": protocol["protocol_id"], "completed": rows,
                   "expected_calls": len(tasks), "launched_at": launched_at})
    totals = {}
    for arm in protocol["arms"]:
        selected = [row for row in rows if row["arm"] == arm]
        total = sum(row["total"] for row in selected); correct = sum(row["correct"] for row in selected)
        totals[arm] = {"calls": len(selected), "answers": total, "correct": correct,
                       "accuracy": correct / total, "unknown_rate": sum(row["unknown"] for row in selected) / total,
                       "false_answer_rate": sum(row["false_answers"] for row in selected) / total,
                       "mean_context_bytes": sum(row.get("context_bytes", 0) for row in selected) / len(selected)}
    result = {"schema_version": 1, "kind": "v2_paper_edair_qa",
              "protocol_id": protocol["protocol_id"],
              "protocol_sha256": hashlib.sha256(protocol_bytes).hexdigest(),
              "status": "complete", "totals": totals, "calls": sorted(
                  rows, key=lambda x: (x["design"], x["arm"], x["repetition"])),
              "claim_boundary": protocol["claim_boundary"]}
    _write(output / "report.json", result)
    print(json.dumps({"output": str(output / "report.json"), "totals": totals}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
