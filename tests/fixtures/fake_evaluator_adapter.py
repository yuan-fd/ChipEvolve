#!/usr/bin/env python3
"""A fake protected evaluator, driven by the evaluated task's behaviour.

It reads the workspace it is asked to evaluate, derives a verdict from what it
finds there, and returns that verdict as a ``protected_evaluation`` artifact.
The behaviours let the tests exercise every way an evaluator can be wrong.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def write_result(path: Path, payload: dict, started_at: str) -> None:
    path.write_text(json.dumps({
        "schema_version": 2, "started_at": started_at, "ended_at": now(), **payload,
    }, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()

    started_at = now()
    result_path = Path(args.result)
    workspace = result_path.parent

    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    inputs = request["task"]["inputs"]
    evaluated = Path(inputs["workspace"])
    behaviour = (
        (inputs.get("evaluated_task") or {}).get("inputs", {}).get("behaviour")
        or "ok"
    )

    if behaviour == "abstain":
        verdict = {
            "status": "incomplete",
            "reason": "the evaluated run produced no stage report to score",
            "metrics": [],
        }
    elif behaviour == "reject":
        verdict = {
            "status": "rejected",
            "reason": "the declared report does not match the measured workspace",
            "metrics": [],
        }
    elif behaviour == "unsourced":
        verdict = {
            "status": "admissible",
            "metrics": [{"name": "area_um2", "value": 1.0, "unit": "um2"}],
        }
    elif behaviour == "phantom_metric":
        verdict = {
            "status": "admissible",
            "metrics": [{
                "name": "area_um2", "value": 1.0, "unit": "um2",
                "context": {"source_artifact_store_key": "not-there.json"},
            }],
        }
    elif behaviour == "escape":
        verdict = {
            "status": "admissible",
            "metrics": [{
                "name": "area_um2", "value": 1.0,
                "context": {"source_artifact_store_key": "../../etc/hosts"},
            }],
        }
    else:
        # Honest: read a number out of the evaluated workspace.
        source = evaluated / "report.json"
        measured = json.loads(source.read_text(encoding="utf-8"))["area_um2"]
        verdict = {
            "status": "admissible",
            "metrics": [{
                "name": "area_um2", "value": measured, "unit": "um2",
                "parser_id": "fake-evaluator", "parser_version": "1.0.0",
                "context": {"source_artifact_store_key": "report.json"},
            }],
            "artifacts": [],
        }

    (workspace / "protected_evaluation.json").write_text(
        json.dumps(verdict, indent=2), encoding="utf-8"
    )
    write_result(result_path, {
        "status": "succeeded",
        "exit_code": 0,
        "artifacts": [
            {"kind": "protected_evaluation", "path": "protected_evaluation.json"},
        ],
    }, started_at)
    return 0


if __name__ == "__main__":
    sys.exit(main())
