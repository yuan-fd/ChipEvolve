#!/usr/bin/env python3
"""Single-host worker for the durable Workflow Runtime queue."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app import ApiState  # noqa: E402


class Heartbeat:
    def __init__(self, path: Path, worker_id: str):
        self.path = path
        self.worker_id = worker_id
        self.active_run: str | None = None
        self.status = "idle"
        self._lock = threading.Lock()

    def write(self) -> None:
        now = datetime.now(timezone.utc)
        payload = {
            "schema_version": 1,
            "pid": os.getpid(),
            "worker_id": self.worker_id,
            "status": self.status,
            "active_run": self.active_run,
            "updated_at": now.isoformat(),
            "updated_at_epoch": now.timestamp(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
            temporary.replace(self.path)


def _oldest_ready_run(state: ApiState):
    ready = {"queued", "retry_wait"}
    runs = [run for run in state.runtime_store.list_runs(limit=500)
            if run.status.value in ready]
    return runs[-1] if runs else None


def main(argv: list[str] | None = None) -> int:
    local_state = Path(os.environ.get(
        "OPENROAD_PLATFORM_LOCAL_STATE", f"/tmp/openroad-platform-{os.getuid()}"
    ))
    parser = argparse.ArgumentParser(description="Workflow Runtime worker")
    parser.add_argument("--db", type=Path, default=ROOT / "var" / "platform.db")
    parser.add_argument("--upload-root", type=Path, default=ROOT / "var" / "uploads")
    parser.add_argument("--design-root", type=Path, default=ROOT / "var" / "designs")
    parser.add_argument("--legacy-root", type=Path,
                        default=Path(os.environ.get("ICCAD_ROOT", ROOT.parent / "iccad")))
    parser.add_argument("--runtime-db", type=Path,
                        default=Path(os.environ.get(
                            "OPENROAD_PLATFORM_RUNTIME_DB", local_state / "runtime.db")))
    parser.add_argument("--campaign-db", type=Path,
                        default=Path(os.environ.get(
                            "OPENROAD_PLATFORM_CAMPAIGN_DB", local_state / "campaign.db")))
    parser.add_argument("--optimization-db", type=Path,
                        default=Path(os.environ.get(
                            "OPENROAD_PLATFORM_OPTIMIZATION_DB", local_state / "optimization.db")))
    parser.add_argument("--orfs-root", type=Path,
                        default=Path(os.environ.get(
                            "ORFS_ROOT", ROOT.parent / "OpenROAD-flow-scripts")))
    heartbeat_default = os.environ.get("OPENROAD_PLATFORM_RUNTIME_WORKER_HEARTBEAT")
    parser.add_argument("--heartbeat", type=Path,
                        default=Path(heartbeat_default) if heartbeat_default else None)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--once", action="store_true",
                        help="Execute at most one ready Runtime stage, then exit")
    args = parser.parse_args(argv)
    if args.poll_seconds <= 0:
        parser.error("--poll-seconds must be positive")
    if args.heartbeat is None:
        args.heartbeat = args.runtime_db.expanduser().resolve().parent / "runtime-worker.heartbeat.json"

    lock_path = args.heartbeat.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_stream = lock_path.open("a+")
    try:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"Workflow Runtime worker is already active ({lock_path})", file=sys.stderr)
        return 2

    state = ApiState(
        args.db, args.upload_root, args.orfs_root,
        design_root=args.design_root, legacy_root=args.legacy_root,
        runtime_db_path=args.runtime_db, campaign_db_path=args.campaign_db,
        optimization_db_path=args.optimization_db, byok_transport_secure=False,
        load_taiwei_plugin=False,
    )
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    state.runtime.worker_id = worker_id
    heartbeat = Heartbeat(args.heartbeat, worker_id)
    stop = threading.Event()

    def request_stop(_signum=None, _frame=None) -> None:
        stop.set()
        if heartbeat.active_run:
            try:
                state.runtime_store.request_cancel(heartbeat.active_run)
            except Exception:
                pass

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    def pulse() -> None:
        while not stop.wait(2.0):
            heartbeat.write()

    pulse_thread = threading.Thread(target=pulse, name="runtime-worker-heartbeat",
                                    daemon=True)
    pulse_thread.start()
    heartbeat.write()
    print(f"Workflow Runtime worker {worker_id} is ready", flush=True)
    try:
        while not stop.is_set():
            state.runtime_store.expire_leases()
            run = _oldest_ready_run(state)
            if run is None:
                if args.once:
                    break
                stop.wait(args.poll_seconds)
                continue
            heartbeat.active_run = run.run_id
            heartbeat.status = "running"
            heartbeat.write()
            print(f"Executing Runtime run {run.run_id} ({run.task_spec.plugin_id})", flush=True)
            try:
                if run.task_spec.plugin_id == "taiwei-pin-3d":
                    state.ensure_taiwei_plugin()
                state.runtime.execute_once(run.run_id)
            except Exception as exc:  # Keep the worker observable if manifest resolution fails.
                print(f"Runtime run {run.run_id} could not start: {exc}", file=sys.stderr,
                      flush=True)
                stop.wait(args.poll_seconds)
            finally:
                heartbeat.active_run = None
                heartbeat.status = "idle"
                heartbeat.write()
            if args.once:
                break
    finally:
        stop.set()
        heartbeat.active_run = None
        heartbeat.status = "stopped"
        heartbeat.write()
        pulse_thread.join(timeout=3)
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)
        lock_stream.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
