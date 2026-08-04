from __future__ import annotations

import socket
import time
import uuid
from pathlib import Path

from openroad_platform_execution import ORFSRunner

from .store import JobStore


class Worker:
    def __init__(
        self,
        store: JobStore,
        *,
        orfs_root: str | Path,
        work_root: str | Path,
        openroad_bin: str | Path | None = None,
        yosys_bin: str | Path | None = None,
        worker_id: str | None = None,
    ):
        self.store = store
        self.worker_id = worker_id or f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self.runner = ORFSRunner(
            orfs_root=orfs_root,
            work_root=work_root,
            openroad_bin=openroad_bin,
            yosys_bin=yosys_bin,
        )

    def run_once(self) -> bool:
        job = self.store.claim_next(self.worker_id)
        if job is None:
            return False
        try:
            plan = self.runner.prepare(job.request)
            if self.store.cancel_requested(job.id):
                self.store.mark_cancelled(job.id)
                return True
            self.store.mark_running(job.id)
            pulse = _Heartbeat(self.store, job.id)
            result = self.runner.run(
                plan,
                cancel_requested=lambda: pulse() or self.store.cancel_requested(job.id),
                on_stage=lambda stage: self.store.record_stage(job.id, {
                    "stage": stage.stage.value,
                    "status": stage.status.value,
                    "returncode": stage.returncode,
                    "seconds": stage.seconds,
                    "message": stage.message,
                }),
            )
            self.store.complete(job.id, result)
        except Exception as exc:
            self.store.fail(job.id, f"{type(exc).__name__}: {exc}")
        return True

    def serve(self, *, poll_seconds: float = 1.0) -> None:
        while True:
            if not self.run_once():
                time.sleep(poll_seconds)


class _Heartbeat:
    def __init__(self, store: JobStore, job_id: str, interval: float = 5.0):
        self.store = store
        self.job_id = job_id
        self.interval = interval
        self.last = 0.0

    def __call__(self) -> bool:
        now = time.monotonic()
        if now - self.last >= self.interval:
            self.store.heartbeat(self.job_id)
            self.last = now
        return False
