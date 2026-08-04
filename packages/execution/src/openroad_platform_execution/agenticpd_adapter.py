"""Run pinned AgenticPD as a black box and convert its trial to a bounded plan."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
for source_root in (
    REPOSITORY_ROOT / "packages/contracts/src",
    REPOSITORY_ROOT / "packages/execution/src",
):
    sys.path.insert(0, str(source_root))

from openroad_platform_contracts import ExperimentCandidate, ExperimentPlan


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    started = _now()
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        task = request["task"]
        mode = task["parameters"]["mode"]
        if mode == "real" and not os.environ.get("DEEPSEEK_API_KEY"):
            return _fail(args.result, started, "credential_unavailable", "DEEPSEEK_API_KEY was not injected")
        source = Path(os.environ["AGENTICPD_SOURCE"]).resolve()
        if _git(source, "rev-parse", "HEAD") != os.environ["AGENTICPD_EXPECTED_COMMIT"]:
            return _fail(args.result, started, "source_mismatch", "AgenticPD commit changed")
        run_parent = source / "runs" / f"{task['inputs']['platform']}_{task['inputs']['design']}"
        before = {path.name for path in run_parent.iterdir()} if run_parent.is_dir() else set()
        command = [
            os.environ.get("PYTHON", os.sys.executable), str(source / "main.py"),
            "--iterations", str(task["parameters"]["iterations"]),
            "--platform", task["inputs"]["platform"], "--design", task["inputs"]["design"],
            "--timeout", str(min(task["timeout_seconds"], 300)),
        ]
        command.extend(["--mock-llm", "--mock-orfs"] if mode == "mock" else [])
        log = args.result.parent / "agenticpd.log"
        lock_path = Path("/tmp/openroad-platform-agenticpd.lock")
        with lock_path.open("a+") as lock, log.open("w", encoding="utf-8") as stream:
            fcntl.flock(lock, fcntl.LOCK_EX)
            completed = subprocess.run(command, cwd=source, stdout=stream, stderr=subprocess.STDOUT, text=True)
        if completed.returncode:
            return _fail(args.result, started, "upstream_failure", "AgenticPD returned non-zero", completed.returncode)
        after = {path.name for path in run_parent.iterdir()} if run_parent.is_dir() else set()
        created = sorted(after - before)
        if len(created) != 1:
            return _fail(args.result, started, "ambiguous_output", f"Expected one new session, got {created}")
        session = run_parent / created[0]
        trials = _latest_successful_trial(session / "trials.jsonl")
        evidence = args.result.parent / "evidence"
        evidence.mkdir()
        for name in ("trials.jsonl", "tree.json", "config_snapshot.json"):
            shutil.copy2(session / name, evidence / name)
        candidate = _candidate(trials)
        plan = ExperimentPlan(
            plan_id=f"agenticpd-{trials['trial_id']}", producer="agenticpd",
            design_id=task["inputs"]["design"], platform=task["inputs"]["platform"],
            baseline_parameters={"core_utilization_pct": 38.0},
            candidates=(candidate,), max_child_runs=1,
            provenance={
                "upstream_commit": os.environ["AGENTICPD_EXPECTED_COMMIT"],
                "proposal_mode": mode, "mock_qor_authoritative": False,
                "session_evidence": "evidence/trials.jsonl",
            },
        )
        plan_path = args.result.parent / "experiment_plan.json"
        _write(plan_path, plan.to_dict())
        _write(args.result, {
            "schema_version": 1, "status": "succeeded", "exit_code": 0,
            "started_at": started, "ended_at": _now(),
            "artifacts": [
                {"kind": "experiment_plan", "path": plan_path.name},
                {"kind": "proposal_evidence", "path": "evidence/trials.jsonl"},
                {"kind": "log", "path": log.name},
            ],
            "metrics": [{"name": "agenticpd.candidates", "value": 1, "unit": "count"}],
            "provenance": plan.provenance,
        })
        return 0
    except Exception as exc:
        return _fail(args.result, started, "adapter_error", f"{type(exc).__name__}: {exc}")


def _latest_successful_trial(path: Path) -> dict:
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    candidates = [item for item in records if item.get("status") == "ok" and item.get("branch_stage")]
    if not candidates:
        raise ValueError("AgenticPD produced no successful candidate")
    return candidates[-1]


def _candidate(trial: dict) -> ExperimentCandidate:
    params = trial.get("params") or {}
    fp = params.get("FP") or {}
    value = fp.get("CORE_UTILIZATION")
    if not isinstance(value, (int, float)) or not 1 <= value <= 99:
        raise ValueError("Candidate CORE_UTILIZATION is missing or invalid")
    unsupported = {
        f"{stage}.{name}": setting
        for stage, mapping in params.items() for name, setting in mapping.items()
        if not (stage == "FP" and name == "CORE_UTILIZATION")
    }
    return ExperimentCandidate(
        candidate_id=f"candidate-{trial['trial_id']}", source_trial_id=trial["trial_id"],
        parameters={"core_utilization_pct": float(value)},
        unsupported_parameters=unsupported,
        evidence_refs=("evidence/trials.jsonl", "evidence/tree.json"),
    )


def _fail(path: Path, started: str, category: str, message: str, code: int = 1) -> int:
    _write(path, {"schema_version": 1, "status": "failed", "exit_code": code,
                  "started_at": started, "ended_at": _now(), "artifacts": [],
                  "metrics": [], "failure": {"category": category, "message": message},
                  "provenance": {}})
    return code


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          stdout=subprocess.PIPE, text=True).stdout.strip()


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
