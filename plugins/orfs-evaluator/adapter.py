#!/usr/bin/env python3
"""Adapter entry point for the ORFS signoff evaluator.

Speaks the platform's request/result protocol and returns a verdict as a
``protected_evaluation`` artifact.  Everything it does is declared in its
manifest; this file only wires the request to the ported evaluation logic.

The platform never reads ORFS JSON itself.  That is the whole reason this file
exists in a plugin rather than in the kernel: the control plane knows how to
invoke an evaluator and how to reject an untraceable answer, and nothing about
what the answer means.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# The script's own directory is on sys.path because Python adds it for the
# main script, so the ported modules import normally.  No sys.path surgery.
from evaluator import (
    EVALUATION_SCHEMA_VERSION,
    evaluate_orfs_run,
    sha256_file,
    write_immutable_evaluation,
)

#: Name of the evaluation artifact relative to the evaluated workspace.
EVALUATION_FILENAME = "common_evaluation.json"

#: Name of this adapter's verdict artifact, and the kind the platform
#: reserves for it.
VERDICT_FILENAME = "protected_evaluation.json"
VERDICT_KIND = "protected_evaluation"
ERROR_FILENAME = "protected_evaluator_error.log"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def inside(root: Path, candidate: Path, *, relative_to: Path | None = None) -> Path:
    """Resolve a path and refuse anything outside the evaluated workspace.

    A relative path is resolved against ``relative_to`` when given.  Letting
    it resolve against the process working directory would make the result
    depend on how the adapter happened to be launched -- an implicit rule
    that works in testing and fails in production.
    """
    resolved_root = root.resolve()
    path = Path(candidate).expanduser()
    if not path.is_absolute() and relative_to is not None:
        path = Path(relative_to) / path
    resolved = path.resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"evaluation input escapes its workspace: {candidate}") from exc
    return resolved


def locate_inputs(workspace: Path) -> tuple[Path, Path, dict, dict]:
    """Find the plan and run result the executing adapter left behind.

    The implementation directory is the evaluated workspace itself, because that
    is where the adapter writes: the kernel hands the evaluator the attempt
    workspace and the adapter owns its root.  An earlier version of this function
    looked for ``orfs/implementation`` under it -- a nesting the frozen platform
    produced and nothing in v2 creates, so *every* evaluation abstained with "no
    ORFS implementation directory" and every run was recorded as unmeasured.

    There is one layout, and a workspace that is not it is named as such rather
    than searched for.
    """
    implementation = Path(workspace).resolve()
    for name in ("plan.json", "run_result.json"):
        if not (implementation / name).is_file():
            raise FileNotFoundError(
                f"no ORFS attempt under {implementation}: {name} is missing"
            )
    plan = read_json(inside(workspace, implementation / "plan.json"))
    run_result = read_json(inside(workspace, implementation / "run_result.json"))
    return (implementation, inside(workspace, implementation / "plan.json"),
            plan, run_result)


def build_verdict(workspace: Path, request_task: dict) -> dict:
    """Run the evaluation and shape it into a verdict.

    Every metric cites the evaluation artifact as its source, so the
    platform can verify that each number traces to a file it can hash.
    """
    implementation, _, plan, run_result = locate_inputs(workspace)
    request = plan["request"]
    design = str(plan["design"])
    platform = str(request["platform"])

    identity = str((request_task.get("labels") or {}).get("design_bundle_sha256") or "")
    if len(identity) != 64 or any(c not in "0123456789abcdef" for c in identity.lower()):
        identity = sha256_file(
            inside(implementation, implementation / "design_input_manifest.json")
        )

    stages = run_result.get("stages") or []
    evaluation = evaluate_orfs_run(
        log_dir=inside(implementation,
                       implementation / "logs" / platform / design / "base"),
        result_dir=inside(implementation,
                          implementation / "results" / platform / design / "base"),
        platform=platform,
        design=design,
        design_identity_sha256=identity,
        effective_config_sha256=sha256_file(
            inside(implementation, Path(str(plan["config_path"])),
                   relative_to=implementation)
        ),
        or_seed=int(request["or_seed"]),
        source_kind="native-platform-orfs",
        clock_period_ns=float(request["clock_period_ns"]),
        runtime_seconds=sum(float(item.get("seconds") or 0.0) for item in stages),
        run_metadata={
            "run_id": str(plan["run_id"]),
            "target_stage": str(request["target_stage"]),
            "stage_statuses": [
                {k: item.get(k) for k in ("stage", "status", "returncode", "seconds")}
                for item in stages if isinstance(item, dict)
            ],
        },
    )

    target = workspace / EVALUATION_FILENAME
    write_immutable_evaluation(target, evaluation)

    metrics = [
        {
            "name": name, "value": value,
            "context": {
                "source_artifact_store_key": EVALUATION_FILENAME,
                "parser_id": "orfs-signoff-evaluator",
                "parser_version": str(EVALUATION_SCHEMA_VERSION),
            },
        }
        for name, value in evaluation["metrics"].items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]

    if evaluation["feasible"]:
        verdict = {"status": "admissible", "metrics": metrics}
    else:
        # A run that fails a signoff gate is rejected, and the reasons travel
        # with the rejection.  It is never reported as "no data".
        verdict = {
            "status": "rejected",
            "reason": "signoff gate failed: " + ", ".join(evaluation["gate"]["reasons"]),
            "metrics": metrics,
            "loss_manifest": {
                "gate_reasons": evaluation["gate"]["reasons"],
                "missing_artifacts": evaluation["gate"]["missing_artifacts"],
                "missing_metrics": evaluation["gate"]["missing_metrics"],
            },
        }
    return verdict


def write_result(path: Path, payload: dict, started_at: str) -> None:
    path.write_text(json.dumps({
        "schema_version": 2, "started_at": started_at, "ended_at": now(), **payload,
    }, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()

    started_at = now()
    # Resolved, and taken from the input the kernel declares rather than from
    # where this process happened to be launched: every path this adapter reports
    # is relative to the evaluated workspace, and a relative one would resolve
    # against whatever directory the caller used.
    request_path = Path(args.request).expanduser().resolve()
    result_path = Path(args.result).expanduser().resolve()

    request = read_json(request_path)
    task = request["task"]
    declared = (task.get("inputs") or {}).get("workspace")
    if not declared:
        raise SystemExit(
            "the request must name the workspace being evaluated: "
            "task.inputs.workspace"
        )
    workspace = Path(str(declared)).expanduser().resolve()

    try:
        verdict = build_verdict(workspace, task)
    except Exception as exc:  # noqa: BLE001 - reported, not hidden
        # An evaluator that cannot read its inputs abstains and says why.  It
        # does not invent a number, and it does not crash the run: the verdict
        # travels to the platform, which records the run as unmeasured.
        reason = f"{type(exc).__name__}: {exc}"
        (workspace / ERROR_FILENAME).write_text(reason + "\n", encoding="utf-8")
        (workspace / VERDICT_FILENAME).write_text(json.dumps({
            "status": "incomplete", "reason": reason, "metrics": [],
        }, indent=2), encoding="utf-8")
        write_result(result_path, {
            "status": "succeeded",
            "exit_code": 0,
            "artifacts": [{"kind": VERDICT_KIND, "path": VERDICT_FILENAME}],
        }, started_at)
        return 0

    (workspace / VERDICT_FILENAME).write_text(
        json.dumps(verdict, indent=2), encoding="utf-8"
    )
    write_result(result_path, {
        "status": "succeeded",
        "exit_code": 0,
        "artifacts": [{"kind": VERDICT_KIND, "path": VERDICT_FILENAME}],
    }, started_at)
    return 0


if __name__ == "__main__":
    sys.exit(main())
