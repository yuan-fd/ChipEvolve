"""Small evidence-preserving compositions over Runtime-owned child runs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openroad_platform_contracts import RuntimeStatus, TaskSpec
from openroad_platform_execution import build_orfs_task

from .runtime import WorkflowRuntime


@dataclass(frozen=True)
class RTLToORFSResult:
    rtl_run_id: str
    orfs_run_id: str | None
    status: RuntimeStatus
    rtl_artifact_sha256: str | None
    failure: str | None = None


def execute_verified_rtl_to_orfs(
    runtime: WorkflowRuntime,
    verification_task: TaskSpec,
    *,
    top: str,
    orfs_options: dict[str, Any] | None = None,
) -> RTLToORFSResult:
    """Only a Runtime-succeeded RTL verification artifact may enter ORFS."""
    if verification_task.plugin_id != "rtl-verify":
        raise ValueError("verification_task must target rtl-verify")
    verify_run = _drain(runtime, runtime.submit(
        verification_task, capability="eda.rtl.verify").run_id)
    if verify_run.status is not RuntimeStatus.SUCCEEDED:
        return RTLToORFSResult(verify_run.run_id, None, verify_run.status, None,
                               verify_run.terminal_reason)
    view = runtime.describe(verify_run.run_id)
    artifact, attempt = _artifact_and_attempt(view, "rtl")
    rtl_path = Path(attempt["workspace"]) / artifact["store_key"]
    actual = _sha256(rtl_path)
    if actual != artifact["sha256"]:
        raise RuntimeError("verified RTL artifact changed before ORFS submission")
    task = build_orfs_task(
        rtl_path, project_id=verification_task.project_id,
        design_id=verification_task.design_id,
        task_id=f"orfs-after-{verification_task.task_id}", top=top,
        labels={"source_run_id": verify_run.run_id, "source_plugin": "rtl-verify"},
        **dict(orfs_options or {}),
    )
    orfs_run = _drain(runtime, runtime.submit(task, capability="eda.rtl_to_gds").run_id)
    return RTLToORFSResult(verify_run.run_id, orfs_run.run_id, orfs_run.status, actual,
                           orfs_run.terminal_reason)


def execute_rtl_to_orfs(
    runtime: WorkflowRuntime,
    rtl_task: TaskSpec,
    *,
    top: str,
    orfs_options: dict[str, Any] | None = None,
) -> RTLToORFSResult:
    """Run RTL generation, then submit its hashed RTL as an ORFS child run.

    This helper never writes run state itself. Both children are ordinary Runtime
    runs, so failures, cancellation, attempts, artifacts and events remain owned
    by the sole scheduling authority.
    """

    if rtl_task.plugin_id != "rtlscout":
        raise ValueError("rtl_task must target rtlscout")
    rtl_run = runtime.submit(rtl_task, capability="agent.rtl.generate")
    rtl_run = _drain(runtime, rtl_run.run_id)
    if rtl_run.status is not RuntimeStatus.SUCCEEDED:
        return RTLToORFSResult(
            rtl_run_id=rtl_run.run_id, orfs_run_id=None, status=rtl_run.status,
            rtl_artifact_sha256=None, failure=rtl_run.terminal_reason,
        )
    view = runtime.describe(rtl_run.run_id)
    rtl_artifact, successful = _artifact_and_attempt(view, "rtl")
    rtl_path = Path(successful["workspace"]) / rtl_artifact["store_key"]
    actual = _sha256(rtl_path)
    if actual != rtl_artifact["sha256"]:
        raise RuntimeError("registered RTL artifact changed before ORFS submission")

    options = dict(orfs_options or {})
    task = build_orfs_task(
        rtl_path,
        project_id=rtl_task.project_id,
        design_id=rtl_task.design_id,
        top=top,
        task_id=f"orfs-after-{rtl_task.task_id}",
        labels={"source_run_id": rtl_run.run_id, "source_plugin": "rtlscout"},
        **options,
    )
    orfs_run = runtime.submit(task, capability="eda.rtl_to_gds")
    orfs_run = _drain(runtime, orfs_run.run_id)
    return RTLToORFSResult(
        rtl_run_id=rtl_run.run_id,
        orfs_run_id=orfs_run.run_id,
        status=orfs_run.status,
        rtl_artifact_sha256=actual,
        failure=orfs_run.terminal_reason,
    )


def _drain(runtime: WorkflowRuntime, run_id: str):
    while True:
        run = runtime.execute_once(run_id)
        if run.status in {
            RuntimeStatus.SUCCEEDED, RuntimeStatus.FAILED,
            RuntimeStatus.CANCELLED, RuntimeStatus.TIMED_OUT,
        }:
            return run


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_and_attempt(view: dict[str, Any], kind: str) -> tuple[dict[str, Any], dict[str, Any]]:
    for stage in view["stages"]:
        for attempt in stage["attempts"]:
            if attempt["status"] != "succeeded":
                continue
            for artifact in attempt["artifacts"]:
                if artifact["kind"] == kind:
                    return artifact, attempt
    raise RuntimeError(f"succeeded run has no registered {kind} artifact")
