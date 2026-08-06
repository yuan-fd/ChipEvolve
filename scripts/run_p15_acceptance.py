#!/usr/bin/env python3
"""Run the fixed DPLEvolve static gate through the authoritative Runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "packages/contracts/src", ROOT / "packages/execution/src",
               ROOT / "packages/scheduler/src"):
    sys.path.insert(0, str(source))

from openroad_platform_contracts import RuntimeStatus  # noqa: E402
from openroad_platform_execution import (  # noqa: E402
    PluginRegistry, build_dplevolve_audit_task, dplevolve_plugin_manifest,
)
from openroad_platform_scheduler import RuntimeStore, WorkflowRuntime  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--source-root", type=Path,
                        default=ROOT / ".external-src/dplevolve")
    args = parser.parse_args()
    output = args.output_root.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(output)
    output.mkdir(parents=True, exist_ok=True)
    lock = json.loads(
        (ROOT / "integrations/dplevolve/source.lock.json").read_text(encoding="utf-8")
    )
    store = RuntimeStore(output / "runtime.db")
    manifest = dplevolve_plugin_manifest(
        args.source_root, sys.executable,
        expected_commit=lock["commit"],
        expected_tree_sha256=lock["content_manifest_sha256"],
        expected_file_count=lock["file_count"],
        default_timeout_seconds=600,
    )
    runtime = WorkflowRuntime(
        store, PluginRegistry([manifest]), workspace_root=output / "runtime-workspaces",
        worker_id="p15-acceptance", lease_seconds=30,
    )
    run = runtime.submit(build_dplevolve_audit_task(
        project_id="p15-real", timeout_seconds=600,
    ))
    completed = runtime.execute_once(run.run_id)
    description = runtime.describe(run.run_id)
    _write(output / "runtime_snapshot.json", description)
    shutil.copy2(output / "runtime.db", output / "runtime.final.db")
    attempt = description["stages"][0]["attempts"][0]
    artifacts = []
    for item in attempt["artifacts"]:
        path = Path(attempt["workspace"]) / item["store_key"]
        artifacts.append({
            "kind": item["kind"], "path": str(path.relative_to(output)),
            "sha256": _sha256(path), "size_bytes": path.stat().st_size,
        })
    accepted = (
        completed.status is RuntimeStatus.SUCCEEDED
        and {item["kind"] for item in artifacts}
        == {"release_gate_log", "source_lock", "audit_report"}
    )
    summary = {
        "schema_version": 1, "phase": "P15", "accepted": accepted,
        "run_id": run.run_id, "runtime_status": completed.status.value,
        "runtime_authoritative": True, "source_commit": lock["commit"],
        "source_tree_sha256": lock["content_manifest_sha256"],
        "source_file_count": lock["file_count"], "license": lock["license"],
        "execution_class": "read-only-source-audit",
        "eda_executed": False, "source_mutated": False,
        "candidate_promotion_applied": False,
        "artifacts": artifacts,
        "runtime_snapshot_sha256": _sha256(output / "runtime_snapshot.json"),
        "runtime_db_sha256": _sha256(output / "runtime.final.db"),
    }
    _write(output / "acceptance_summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if accepted else 2


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
