#!/usr/bin/env python3
"""Exercise real TaiWei failure, timeout and detached-child cancellation paths."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "packages/contracts/src", ROOT / "packages/execution/src",
               ROOT / "packages/scheduler/src"):
    sys.path.insert(0, str(source))

from openroad_platform_contracts import RuntimeStatus  # noqa: E402
from openroad_platform_execution import (  # noqa: E402
    PluginRegistry, TaiWeiToolchainProfile, build_taiwei_task, taiwei_plugin_manifest,
)
from openroad_platform_scheduler import RuntimeStore, WorkflowRuntime  # noqa: E402


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def _profile() -> tuple[Path, TaiWeiToolchainProfile]:
    tool_root = ROOT / ".tools/taiwei-official-3d"
    orfs = tool_root / "orfs-research"
    return ROOT / ".external-src/taiwei-pin-3d", TaiWeiToolchainProfile(
        orfs_root=orfs,
        openroad_bin=orfs / "tools/install/OpenROAD/bin/openroad",
        yosys_bin=orfs / "tools/install/yosys/bin/yosys",
        runtime_library_paths=(
            tool_root / "dependencies/lib", tool_root / "dependencies/lib64",
            Path("/opt/openEuler/gcc-toolset-12/root/usr/lib64"),
        ),
    )


def _runtime(store: RuntimeStore, manifest, workspace: Path, worker: str) -> WorkflowRuntime:
    return WorkflowRuntime(store, PluginRegistry([manifest]), workspace_root=workspace,
                           worker_id=worker, lease_seconds=30)


def _live_workspace_processes(workspace: Path) -> list[dict]:
    needle = str(workspace.resolve()).encode()
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes()
            stat = (entry / "stat").read_text(encoding="utf-8")
            state = stat[stat.rfind(")") + 2:].split()[0]
        except (FileNotFoundError, PermissionError, ProcessLookupError, IndexError):
            continue
        if state != "Z" and needle in cmdline:
            found.append({"pid": int(entry.name),
                          "command": cmdline.replace(b"\0", b" ").decode(errors="replace")})
    return found


def _wait_clean(workspace: Path, seconds: float = 10.0) -> list[dict]:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = _live_workspace_processes(workspace)
        if not found:
            return []
        time.sleep(0.1)
    return _live_workspace_processes(workspace)


def _execute(runtime: WorkflowRuntime, run_id: str, errors: list[str]) -> None:
    try:
        runtime.execute_once(run_id)
    except Exception as exc:  # pragma: no cover - recorded as acceptance evidence
        errors.append(f"{type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--num-cores", type=int, default=4)
    args = parser.parse_args()
    output = args.output_root.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(output)
    output.mkdir(parents=True, exist_ok=True)

    source, profile = _profile()
    manifest = taiwei_plugin_manifest(source, profile, default_timeout_seconds=600,
                                      num_cores=args.num_cores)
    live_root = Path("/tmp") / f"openroad-platform-p8-real-resilience-{uuid.uuid4().hex}"
    store = RuntimeStore(live_root / "runtime.db")

    # A one-second real adapter deadline must be durable and leave no child process.
    timeout_runtime = _runtime(store, manifest, output / "timeout-workspaces", "p8r-timeout")
    timeout_run = timeout_runtime.submit(build_taiwei_task(
        project_id="p8-real-resilience", timeout_seconds=1,
        task_id="p8-real-timeout",
    ))
    timeout_result = timeout_runtime.execute_once(timeout_run.run_id)
    timeout_view = timeout_runtime.describe(timeout_run.run_id)
    timeout_attempt = timeout_view["stages"][0]["attempts"][0]
    timeout_workspace = Path(timeout_attempt["workspace"])
    timeout_orphans = _wait_clean(timeout_workspace)
    if (timeout_result.status is not RuntimeStatus.FAILED
            or timeout_result.terminal_reason != "timed_out"
            or timeout_attempt["status"] != RuntimeStatus.TIMED_OUT.value
            or timeout_orphans):
        raise RuntimeError(f"TaiWei timeout cleanup failed: {timeout_orphans}")

    # Force the pinned flow to fail at tool invocation without modifying fixed source.
    failed_environment = dict(manifest.environment)
    failed_environment["YOSYS_EXE"] = "/bin/false"
    failure_manifest = replace(manifest, environment=failed_environment)
    failure_runtime = _runtime(store, failure_manifest, output / "failure-workspaces",
                               "p8r-failure")
    failure_run = failure_runtime.submit(build_taiwei_task(
        project_id="p8-real-resilience", timeout_seconds=300,
        task_id="p8-real-failure",
    ))
    failure_result = failure_runtime.execute_once(failure_run.run_id)
    failure_view = failure_runtime.describe(failure_run.run_id)
    failure_attempt = failure_view["stages"][0]["attempts"][0]
    failure_orphans = _wait_clean(Path(failure_attempt["workspace"]))
    if failure_result.status is not RuntimeStatus.FAILED or failure_orphans:
        raise RuntimeError(f"TaiWei failure evidence/cleanup failed: {failure_orphans}")

    # Cancel only after the official launcher has detached its task into a new session.
    cancel_runtime = _runtime(store, manifest, output / "cancel-workspaces", "p8r-cancel")
    cancel_run = cancel_runtime.submit(build_taiwei_task(
        project_id="p8-real-resilience", timeout_seconds=600,
        task_id="p8-real-cancel",
    ))
    errors: list[str] = []
    thread = threading.Thread(target=_execute,
                              args=(cancel_runtime, cancel_run.run_id, errors), daemon=True)
    thread.start()
    cancel_workspace = None
    dispatch_seen = False
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        stages = store.list_stages(cancel_run.run_id)
        attempts = store.list_attempts(stages[0].stage_run_id)
        if attempts:
            cancel_workspace = Path(attempts[0].workspace)
            status_files = list(cancel_workspace.glob(
                "taiwei-source/run_logs/status/ord__asap7_3D__gcd.json"
            ))
            if status_files:
                try:
                    status = json.loads(status_files[0].read_text(encoding="utf-8"))
                    dispatch_seen = bool(status.get("dispatch_pid"))
                except (OSError, json.JSONDecodeError):
                    pass
            if dispatch_seen:
                break
        time.sleep(0.1)
    if not dispatch_seen or cancel_workspace is None:
        raise RuntimeError("Official TaiWei detached dispatch did not become observable")
    store.request_cancel(cancel_run.run_id)
    thread.join(timeout=30)
    if thread.is_alive() or errors:
        raise RuntimeError(f"TaiWei cancellation worker did not stop: {errors}")
    cancel_view = cancel_runtime.describe(cancel_run.run_id)
    cancel_result = store.get_run(cancel_run.run_id)
    cancel_orphans = _wait_clean(cancel_workspace)
    if cancel_result.status is not RuntimeStatus.CANCELLED or cancel_orphans:
        raise RuntimeError(f"TaiWei detached child survived cancellation: {cancel_orphans}")

    snapshots = {
        "timeout": timeout_view,
        "failure": failure_view,
        "cancel": cancel_view,
    }
    _write(output / "runtime_resilience_runs.json", snapshots)
    all_events = {name: view["events"] for name, view in snapshots.items()}
    _write(output / "runtime_resilience_events.json", all_events)
    backup = output / "runtime-resilience.db.snapshot"
    with sqlite3.connect(store.path) as source_db, sqlite3.connect(backup) as backup_db:
        source_db.backup(backup_db)
    with sqlite3.connect(backup) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError(f"Resilience DB backup failed integrity check: {integrity}")

    summary = {
        "schema_version": 1, "phase": "P8-Real-resilience", "accepted": True,
        "runtime_db": str(store.path), "runtime_db_under_tmp": str(store.path).startswith("/tmp/"),
        "timeout": {"run_id": timeout_run.run_id, "status": timeout_attempt["status"],
                    "run_status": timeout_result.status.value,
                    "terminal_reason": timeout_result.terminal_reason,
                    "orphan_processes": timeout_orphans},
        "failure": {"run_id": failure_run.run_id, "status": failure_result.status.value,
                    "category": failure_attempt["failure"]["category"],
                    "orphan_processes": failure_orphans},
        "cancel": {"run_id": cancel_run.run_id, "status": cancel_result.status.value,
                   "official_detached_dispatch_observed": dispatch_seen,
                   "orphan_processes": cancel_orphans},
        "failure_evidence_preserved": all(
            len(view["stages"][0]["attempts"]) == 1 for view in snapshots.values()
        ),
        "database_backup_integrity": integrity,
    }
    _write(output / "resilience_summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
