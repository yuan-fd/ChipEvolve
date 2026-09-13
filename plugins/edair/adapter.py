#!/usr/bin/env python3
"""Adapter entry point for the EDAIR indexing capability.

Turns one raw EDA artifact into a bounded, provenance-bearing index.  The index
is not a replacement for the artifact: the platform keeps and hashes the raw
file, and the index says what it did not represent.

Nothing here decides whether a run is good.  Indexing is not evaluation.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# The script's own directory is on sys.path because Python adds it for the main
# script, so sibling modules import normally.  No sys.path surgery.
from opensta import parse_opensta_paths

PROGRESS_MARKER = "[progress]"

#: Artifact kind this capability produces.
INDEX_KIND = "report"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def report(stage: str, phase: str, **extra) -> None:
    print(PROGRESS_MARKER + " " + json.dumps(
        {"stage": stage, "phase": phase, **extra}
    ), flush=True)


def inside(root: Path, candidate: str) -> Path:
    """Resolve a declared input and refuse anything outside the workspace."""
    path = (root / candidate).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"input escapes the workspace: {candidate!r}") from exc
    return path


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
    result_path = Path(args.result)
    workspace = result_path.parent

    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    inputs = request["task"].get("inputs") or {}

    def fail(category: str, message: str, exit_code: int = 1) -> int:
        write_result(result_path, {
            "status": "failed", "exit_code": exit_code,
            "failure": {"category": category, "message": message,
                        "retryable": False},
            "artifacts": [],
        }, started_at)
        return exit_code

    relative = inputs.get("timing_report")
    if not isinstance(relative, str) or not relative:
        return fail("invalid_input", "inputs.timing_report is required", 3)

    try:
        source = inside(workspace, relative)
    except ValueError as exc:
        return fail("invalid_input", str(exc), 3)

    report("parse", "started")
    if not source.is_file():
        report("parse", "finished", status="failed")
        return fail("missing_input",
                    f"timing report not found in the workspace: {relative}", 4)

    try:
        index = parse_opensta_paths(
            source, max_paths=int(inputs.get("max_paths") or 256))
    except ValueError as exc:
        report("parse", "finished", status="failed")
        return fail("invalid_input", str(exc), 3)
    report("parse", "finished", status="succeeded",
           paths=len(index["paths"]), unparsed=index["unparsed_blocks"])

    index_path = workspace / "timing_paths.index.json"
    index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")

    write_result(result_path, {
        "status": "succeeded",
        "exit_code": 0,
        "artifacts": [
            {"kind": INDEX_KIND, "path": index_path.name},
            # The raw report is registered too.  An index whose source is not
            # kept cannot be checked, and the platform's rule is that a derived
            # view is an index with references rather than a replacement.
            {"kind": "log", "path": relative},
        ],
    }, started_at)
    return 0


if __name__ == "__main__":
    sys.exit(main())
