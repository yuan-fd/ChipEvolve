#!/usr/bin/env python3
"""Execute and verify the real P2 ORFS-plugin acceptance run."""

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
    ROOT / "packages/analysis/src",
):
    sys.path.insert(0, str(source_root))

from openroad_platform_contracts import RuntimeStatus  # noqa: E402
from openroad_platform_execution import (  # noqa: E402
    PluginRegistry,
    ToolchainConfig,
    build_orfs_task,
    orfs_plugin_manifest,
)
from openroad_platform_scheduler import RuntimeStore, WorkflowRuntime  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--rtl", type=Path, default=ROOT / "tests/fixtures/p2_mux_2to1.v"
    )
    parser.add_argument("--orfs-root", type=Path, default=Path.home() / "OpenROAD-flow-scripts")
    parser.add_argument("--openroad-bin", type=Path, default=Path.home() / "bin/openroad")
    parser.add_argument("--yosys-bin", type=Path, default=Path.home() / "bin/yosys")
    parser.add_argument("--klayout-bin", type=Path, default=Path.home() / "bin/klayout")
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--stage-timeout", type=int, default=3600)
    parser.add_argument(
        "--runtime-db",
        type=Path,
        help="Node-local SQLite path; defaults below /tmp to avoid WAL on shared filesystems",
    )
    args = parser.parse_args()

    output = args.output_root.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Acceptance output is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    runtime_db = (
        args.runtime_db.expanduser().resolve()
        if args.runtime_db else
        Path("/tmp/openroad-platform-p2-runtime") / f"{output.name}.db"
    )
    runtime_db.parent.mkdir(parents=True, exist_ok=True)
    if runtime_db.exists():
        raise FileExistsError(f"Runtime DB already exists: {runtime_db}")
    toolchain = ToolchainConfig(
        name="orfs-2d-baseline",
        orfs_root=args.orfs_root.expanduser().resolve(),
        openroad_bin=args.openroad_bin.expanduser().resolve(),
        yosys_bin=args.yosys_bin.expanduser().resolve(),
        klayout_bin=args.klayout_bin.expanduser().resolve(),
    )
    toolchain.validate()
    before = shared_snapshot(toolchain)
    store = RuntimeStore(runtime_db)
    runtime = WorkflowRuntime(
        store,
        PluginRegistry([orfs_plugin_manifest(
            toolchain, default_timeout_seconds=args.timeout,
        )]),
        workspace_root=output / "attempts",
        worker_id="p2-real-acceptance",
        lease_seconds=60,
    )
    task = build_orfs_task(
        args.rtl,
        task_id="p2-real-nangate45-mux",
        project_id="openroad-platform",
        design_id="p2-mux-2to1",
        top="mux_2to1",
        platform_name="nangate45",
        target_stage="finish",
        clock_period_ns=10.0,
        core_utilization_pct=10.0,
        place_density=0.45,
        stage_timeout_seconds=args.stage_timeout,
        timeout_seconds=args.timeout,
        labels={"phase": "P2", "acceptance": "real-nangate45"},
    )
    run = runtime.submit(task, capability="eda.rtl_to_gds")
    print(f"[p2] run_id={run.run_id} workspace={output}", flush=True)
    started = time.monotonic()
    completed = runtime.execute_once(
        run.run_id, on_line=lambda line: print(line, end="", flush=True)
    )
    seconds = round(time.monotonic() - started, 3)
    view = runtime.describe(run.run_id)
    after = shared_snapshot(toolchain)
    attempt = view["stages"][0]["attempts"][0]
    evidence = verify_attempt(attempt)
    run_result = json.loads(evidence["run_result_path"].read_text(encoding="utf-8"))
    runtime_snapshot = archive_runtime_db(runtime_db, output / "runtime.snapshot.db")
    shared_unchanged = before == after
    accepted = (
        completed.status is RuntimeStatus.SUCCEEDED
        and run_result["milestones"]["implementation_valid"] is True
        and run_result["milestones"]["gds_complete"] is True
        and shared_unchanged
    )
    summary = {
        "schema_version": 1,
        "accepted": accepted,
        "seconds": seconds,
        "run": view["run"],
        "stage": view["stages"][0],
        "events": view["events"],
        "artifact_verification": evidence["records"],
        "milestones": run_result["milestones"],
        "shared_toolchain_unchanged": shared_unchanged,
        "shared_toolchain_before": before,
        "shared_toolchain_after": after,
        "runtime_db": {
            "live_path": str(runtime_db),
            "snapshot": runtime_snapshot,
        },
    }
    write_json(output / "acceptance_summary.json", summary)
    print(
        f"[p2] status={completed.status.value} accepted={accepted} "
        f"seconds={seconds} artifacts={len(evidence['records'])}",
        flush=True,
    )
    return 0 if accepted else 2


def verify_attempt(attempt: dict) -> dict:
    workspace = Path(attempt["workspace"])
    required = {"gds", "def", "netlist", "odb", "config",
                "toolchain_snapshot", "run_result"}
    records = []
    paths = {}
    for artifact in attempt["artifacts"]:
        path = (workspace / artifact["store_key"]).resolve()
        if not path.is_file() or path.stat().st_size != artifact["size_bytes"]:
            raise RuntimeError(f"Artifact size mismatch: {path}")
        digest = sha256(path)
        if digest != artifact["sha256"]:
            raise RuntimeError(f"Artifact SHA-256 mismatch: {path}")
        records.append({
            "kind": artifact["kind"], "store_key": artifact["store_key"],
            "size_bytes": artifact["size_bytes"], "sha256": digest,
        })
        paths.setdefault(artifact["kind"], path)
    missing = sorted(required - set(paths))
    if missing:
        raise RuntimeError(f"Required artifact kinds missing: {', '.join(missing)}")
    return {"records": records, "run_result_path": paths["run_result"]}


def shared_snapshot(toolchain: ToolchainConfig) -> dict:
    orfs = toolchain.orfs_root
    openroad_repo = orfs / "tools/OpenROAD"
    return {
        "orfs_head": command(["git", "-C", str(orfs), "rev-parse", "HEAD"]),
        "orfs_status": command([
            "git", "-C", str(orfs), "status", "--porcelain=v2", "--untracked-files=all",
        ]),
        "orfs_diff_sha256": text_sha256(command([
            "git", "-C", str(orfs), "diff", "--binary", "HEAD",
        ])),
        "openroad_status": command([
            "git", "-C", str(openroad_repo), "status", "--porcelain=v2",
        ]) if openroad_repo.is_dir() else "missing",
        "openroad_diff_sha256": text_sha256(command([
            "git", "-C", str(openroad_repo), "diff", "--binary", "HEAD",
        ])) if openroad_repo.is_dir() else None,
        "files": {
            "openroad": file_record(toolchain.openroad_bin),
            "yosys": file_record(toolchain.yosys_bin),
            "klayout": file_record(toolchain.klayout_bin),
            "nangate45_config": file_record(
                toolchain.flow_home / "platforms/nangate45/config.mk"
            ),
        },
    }


def archive_runtime_db(source: Path, destination: Path) -> dict:
    with sqlite3.connect(source, timeout=30) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copyfile(source, destination)
    return file_record(destination)


def command(argv: list[str]) -> str:
    result = subprocess.run(
        argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, timeout=60, check=False,
    )
    return result.stdout.rstrip()


def file_record(path: Path | None) -> dict | None:
    if path is None:
        return None
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def write_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
