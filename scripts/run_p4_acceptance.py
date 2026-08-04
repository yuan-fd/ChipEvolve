#!/usr/bin/env python3
"""Run the real pinned RTLScout fake smoke and feed its RTL to real ORFS."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for source_root in (
    ROOT / "packages/contracts/src",
    ROOT / "packages/execution/src",
    ROOT / "packages/scheduler/src",
):
    sys.path.insert(0, str(source_root))

from openroad_platform_contracts import RuntimeStatus  # noqa: E402
from openroad_platform_execution import (  # noqa: E402
    PluginRegistry,
    ToolchainConfig,
    build_rtlscout_task,
    orfs_plugin_manifest,
    rtlscout_plugin_manifest,
)
from openroad_platform_scheduler import (  # noqa: E402
    RuntimeStore,
    WorkflowRuntime,
    execute_rtl_to_orfs,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--runtime-db", type=Path)
    parser.add_argument("--rtlscout-source", type=Path, default=ROOT / ".external-src/rtlscout")
    parser.add_argument("--rtlscout-python", type=Path, default=ROOT / ".tools/venvs/rtlscout312/bin/python")
    parser.add_argument("--verilator-bin", type=Path, default=ROOT / ".tools/verilator-5.040/bin/verilator")
    parser.add_argument("--orfs-root", type=Path, default=Path.home() / "OpenROAD-flow-scripts")
    parser.add_argument("--openroad-bin", type=Path, default=Path.home() / "bin/openroad")
    parser.add_argument("--yosys-bin", type=Path, default=Path.home() / "bin/yosys")
    parser.add_argument("--klayout-bin", type=Path, default=Path.home() / "bin/klayout")
    parser.add_argument("--timeout", type=int, default=7200)
    args = parser.parse_args()

    output = args.output_root.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Acceptance output is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    db = (args.runtime_db.expanduser().resolve() if args.runtime_db else
          Path("/tmp/openroad-platform-p4-runtime") / f"{output.name}.db")
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        raise FileExistsError(f"Runtime DB already exists: {db}")

    source = args.rtlscout_source.expanduser().resolve()
    source_before = source_snapshot(source)
    orfs = ToolchainConfig(
        name="orfs-2d-baseline", orfs_root=args.orfs_root.expanduser().resolve(),
        openroad_bin=args.openroad_bin.expanduser().resolve(),
        yosys_bin=args.yosys_bin.expanduser().resolve(),
        klayout_bin=args.klayout_bin.expanduser().resolve(),
    )
    orfs.validate()
    registry = PluginRegistry([
        rtlscout_plugin_manifest(
            source, args.rtlscout_python,
            verilator_bin=args.verilator_bin, yosys_bin=args.yosys_bin,
            default_timeout_seconds=args.timeout,
        ),
        orfs_plugin_manifest(orfs, default_timeout_seconds=args.timeout),
    ])
    store = RuntimeStore(db)
    runtime = WorkflowRuntime(
        store, registry, workspace_root=output / "attempts",
        worker_id="p4-real-acceptance", lease_seconds=60,
    )
    task = build_rtlscout_task(
        task_id="p4-rtlscout-simple-adder-fake",
        project_id="openroad-platform", design_id="p4-simple-adder",
        benchmark="simple_adder", model="fake:simple_adder_pass",
        max_steps=3, cost_metric="transistors", timeout_seconds=args.timeout,
        labels={"phase": "P4", "acceptance": "real-tools-offline-model"},
    )
    started = time.monotonic()
    result = execute_rtl_to_orfs(
        runtime, task, top="adder",
        orfs_options={
            "platform_name": "nangate45", "target_stage": "finish",
            "clock_period_ns": 10.0, "core_utilization_pct": 10.0,
            "place_density": 0.45, "stage_timeout_seconds": min(args.timeout, 3600),
            "timeout_seconds": args.timeout,
        },
    )
    rtl_view = runtime.describe(result.rtl_run_id)
    orfs_view = runtime.describe(result.orfs_run_id) if result.orfs_run_id else None
    rtl_artifacts = verify_artifacts(rtl_view)
    orfs_artifacts = verify_artifacts(orfs_view) if orfs_view else []
    source_after = source_snapshot(source)
    gds = next((item for item in orfs_artifacts if item["kind"] == "gds"), None)
    accepted = (
        result.status is RuntimeStatus.SUCCEEDED
        and result.rtl_artifact_sha256 is not None
        and {item["kind"] for item in rtl_artifacts} >= {"rtl", "rtlscout_result", "report"}
        and gds is not None and gds["size_bytes"] > 0
        and source_before == source_after
    )
    snapshot = archive_db(db, output / "runtime.snapshot.db")
    summary = {
        "schema_version": 1,
        "phase": "P4",
        "accepted": accepted,
        "seconds": round(time.monotonic() - started, 3),
        "model_mode": "official offline fake; no credential used",
        "real_llm": {"executed": False, "blocker": "no user-provided provider credential/budget"},
        "rtl_to_orfs": {
            "status": result.status.value,
            "rtl_run_id": result.rtl_run_id,
            "orfs_run_id": result.orfs_run_id,
            "rtl_sha256": result.rtl_artifact_sha256,
        },
        "rtl_run": rtl_view,
        "orfs_run": orfs_view,
        "rtl_artifact_verification": rtl_artifacts,
        "orfs_artifact_verification": orfs_artifacts,
        "source_unchanged": source_before == source_after,
        "source_before": source_before,
        "source_after": source_after,
        "runtime_db": {"live_path": str(db), "snapshot": snapshot},
    }
    write_json(output / "acceptance_summary.json", summary)
    print(
        f"[p4] status={result.status.value} accepted={accepted} "
        f"rtl={result.rtl_run_id} orfs={result.orfs_run_id}", flush=True,
    )
    return 0 if accepted else 2


def verify_artifacts(view: dict | None) -> list[dict]:
    if view is None:
        return []
    records = []
    for stage in view["stages"]:
        for attempt in stage["attempts"]:
            workspace = Path(attempt["workspace"])
            for artifact in attempt["artifacts"]:
                path = workspace / artifact["store_key"]
                digest = sha256(path)
                if path.stat().st_size != artifact["size_bytes"] or digest != artifact["sha256"]:
                    raise RuntimeError(f"Artifact verification failed: {path}")
                records.append({
                    "kind": artifact["kind"], "store_key": artifact["store_key"],
                    "size_bytes": artifact["size_bytes"], "sha256": digest,
                })
    return records


def source_snapshot(source: Path) -> dict:
    return {
        "head": command(["git", "-C", str(source), "rev-parse", "HEAD"]),
        "status": command(["git", "-C", str(source), "status", "--porcelain=v1"]),
        "submodules": command(["git", "-C", str(source), "submodule", "status", "--recursive"]),
    }


def archive_db(source: Path, destination: Path) -> dict:
    with sqlite3.connect(source, timeout=30) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copyfile(source, destination)
    return {"path": str(destination), "size_bytes": destination.stat().st_size,
            "sha256": sha256(destination)}


def command(argv: list[str]) -> str:
    completed = subprocess.run(
        argv, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, timeout=60,
    )
    return completed.stdout.rstrip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
