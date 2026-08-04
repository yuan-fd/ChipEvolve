"""Minimal single-host Workflow Runtime over the generic plugin protocol."""

from __future__ import annotations

import platform
import socket
import time
import uuid
from pathlib import Path
from typing import Callable

from openroad_platform_contracts import RuntimeStatus, TaskSpec
from openroad_platform_execution import PluginRegistry, ProcessAdapter

from .runtime_store import RuntimeRun, RuntimeStore


class WorkflowRuntime:
    def __init__(
        self,
        store: RuntimeStore,
        registry: PluginRegistry,
        *,
        workspace_root: str | Path,
        adapter: ProcessAdapter | None = None,
        worker_id: str | None = None,
        lease_seconds: int = 30,
    ):
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.store = store
        self.registry = registry
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.adapter = adapter or ProcessAdapter()
        self.worker_id = worker_id or f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self.lease_seconds = lease_seconds

    def submit(
        self,
        task: TaskSpec,
        *,
        plugin_version: str | None = None,
        capability: str | None = None,
    ) -> RuntimeRun:
        task.validate()
        if task.plugin_id is None:
            raise ValueError("P1 WorkflowRuntime only supports direct plugin TaskSpec")
        manifest = self.registry.resolve(
            task.plugin_id,
            version=plugin_version,
            capability=capability,
            arch=platform.machine(),
        )
        run, _ = self.store.submit_plugin_run(
            task, plugin_version=manifest.plugin_version
        )
        return run

    def execute_once(
        self,
        run_id: str,
        *,
        on_line: Callable[[str], None] | None = None,
    ) -> RuntimeRun:
        run = self.store.get_run(run_id)
        stages = self.store.list_stages(run_id)
        ready = next(
            (stage for stage in stages
             if stage.status in {RuntimeStatus.QUEUED, RuntimeStatus.RETRY_WAIT}),
            None,
        )
        if ready is None:
            return run
        manifest = self.registry.resolve(
            ready.plugin_id, version=ready.plugin_version, arch=platform.machine()
        )
        attempt_number = len(self.store.list_attempts(ready.stage_run_id)) + 1
        workspace = (
            self.workspace_root / run_id / ready.stage_run_id / f"attempt-{attempt_number}"
        )
        attempt = self.store.start_attempt(
            ready.stage_run_id,
            worker_id=self.worker_id,
            workspace=workspace,
            lease_seconds=self.lease_seconds,
        )
        pulse = _LeasePulse(
            self.store, run_id, attempt.attempt_id,
            worker_id=self.worker_id, lease_seconds=self.lease_seconds,
        )
        try:
            execution = self.adapter.execute(
                manifest,
                run.task_spec,
                workspace=workspace,
                cancel_requested=pulse,
                on_line=on_line,
            )
            if execution.result.status is RuntimeStatus.SUCCEEDED:
                self.store.register_artifacts(attempt.attempt_id, execution.artifacts)
                self.store.register_metrics(attempt.attempt_id, execution.result.metrics)
            self.store.finish_attempt(
                attempt.attempt_id,
                execution.result.status,
                exit_code=execution.result.exit_code,
                failure=execution.result.failure,
            )
        except Exception as exc:
            self.store.finish_attempt(
                attempt.attempt_id,
                RuntimeStatus.FAILED,
                exit_code=1,
                failure={"category": "runtime_error", "message": f"{type(exc).__name__}: {exc}"},
            )
        return self.store.get_run(run_id)

    def describe(self, run_id: str) -> dict:
        return self.store.describe_run(run_id)


class _LeasePulse:
    def __init__(
        self,
        store: RuntimeStore,
        run_id: str,
        attempt_id: str,
        *,
        worker_id: str,
        lease_seconds: int,
    ):
        self.store = store
        self.run_id = run_id
        self.attempt_id = attempt_id
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.last = 0.0

    def __call__(self) -> bool:
        run = self.store.get_run(self.run_id)
        if run.status is RuntimeStatus.CANCEL_REQUESTED:
            return True
        now = time.monotonic()
        interval = max(1.0, self.lease_seconds / 3)
        if now - self.last >= interval:
            self.store.heartbeat(
                self.attempt_id,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )
            self.last = now
        return False
