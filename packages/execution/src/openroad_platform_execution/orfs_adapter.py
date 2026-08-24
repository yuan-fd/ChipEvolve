#!/usr/bin/env python3
"""ORFS v1 task/result-file adapter entrypoint."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from threading import Event


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
for source_root in (
    REPOSITORY_ROOT / "packages/contracts/src",
    REPOSITORY_ROOT / "packages/execution/src",
    REPOSITORY_ROOT / "packages/analysis/src",
    REPOSITORY_ROOT / "packages/visualization/src",
):
    sys.path.insert(0, str(source_root))

from openroad_platform_contracts import (  # noqa: E402
    PluginResult,
    RunRequest,
    RunStage,
    RunStatus,
    RuntimeStatus,
    TaskSpec,
)
from openroad_platform_execution.orfs_plugin import (  # noqa: E402
    ORFS_PLUGIN_ID,
    ORFS_PLUGIN_VERSION,
)
from openroad_platform_execution.orfs_runner import ORFSRunner  # noqa: E402
from openroad_platform_execution.process_guardian import ProcessGuardian  # noqa: E402
from openroad_platform_execution.toolchain import ToolchainConfig  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    started = _now()
    cancelled = Event()

    def request_cancel(_signum, _frame) -> None:
        cancelled.set()

    if os.name == "posix":
        signal.signal(signal.SIGTERM, request_cancel)
        signal.signal(signal.SIGINT, request_cancel)

    try:
        payload = json.loads(args.request.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1:
            raise ValueError("Unsupported adapter request schema_version")
        plugin = payload.get("plugin", {})
        if plugin != {
            "plugin_id": ORFS_PLUGIN_ID,
            "plugin_version": ORFS_PLUGIN_VERSION,
        }:
            raise ValueError("Adapter request plugin identity mismatch")
        task = TaskSpec.from_dict(payload["task"])
        workspace = args.result.expanduser().resolve().parent
        staged_rtl = _stage_rtl(task, workspace)
        request = _legacy_request(task, staged_rtl)
        toolchain = ToolchainConfig.from_environment(
            name=os.environ.get("OPENROAD_PLATFORM_TOOLCHAIN_PROFILE", "default")
        )
        runner = ORFSRunner(
            work_root=workspace / "orfs",
            toolchain=toolchain,
            guardian=ProcessGuardian(poll_interval=0.1, terminate_grace=2.0),
        )
        plan = runner.prepare(request)
        result = runner.run(
            plan,
            cancel_requested=cancelled.is_set,
            on_stage_start=lambda stage: print(
                f"[orfs-stage-start] {stage.value}", flush=True
            ),
            on_stage=lambda stage: print(
                f"[orfs-stage] {stage.stage.value} {stage.status.value} "
                f"{stage.seconds:.3f}s",
                flush=True,
            ),
        )
        plugin_result = _plugin_result(
            result, plan_workdir=Path(plan.workdir), workspace=workspace,
            input_reference=task.inputs["rtl"],
        )
        _write_result(args.result, plugin_result)
        return plugin_result.exit_code
    except Exception as exc:
        failed = PluginResult(
            status=RuntimeStatus.FAILED,
            exit_code=1,
            started_at=started,
            ended_at=_now(),
            failure={
                "category": "adapter_error",
                "message": f"{type(exc).__name__}: {exc}",
            },
            provenance={"adapter": f"{ORFS_PLUGIN_ID}@{ORFS_PLUGIN_VERSION}"},
        )
        _write_result(args.result, failed)
        return 1


def _stage_rtl(task: TaskSpec, workspace: Path) -> Path:
    reference = task.inputs.get("rtl")
    if not isinstance(reference, dict):
        raise ValueError("Task inputs.rtl must be an artifact reference")
    source = Path(str(reference.get("path", ""))).expanduser().resolve()
    expected_size = reference.get("size_bytes")
    expected_sha = reference.get("sha256")
    if not source.is_file() or source.stat().st_size == 0:
        raise FileNotFoundError(f"RTL source is missing or empty: {source}")
    if source.stat().st_size != expected_size or _sha256(source) != expected_sha:
        raise ValueError("RTL source size/SHA-256 does not match TaskSpec")
    destination = workspace / "inputs" / "design.v"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    if destination.stat().st_size != expected_size or _sha256(destination) != expected_sha:
        raise ValueError("Staged RTL size/SHA-256 does not match TaskSpec")
    return destination


def _legacy_request(task: TaskSpec, staged_rtl: Path) -> RunRequest:
    parameters = task.parameters
    target = RunStage(str(parameters.get("target_stage", "finish")))
    return RunRequest(
        rtl_path=str(staged_rtl),
        top=_optional_string(task.inputs.get("top"), "top"),
        clock=_optional_string(task.inputs.get("clock"), "clock"),
        clock_period_ns=float(parameters.get("clock_period_ns", 10.0)),
        platform=str(parameters.get("platform", "nangate45")),
        target_stage=target,
        core_utilization_pct=float(parameters.get("core_utilization_pct", 10.0)),
        place_density=float(parameters.get("place_density", 0.45)),
        or_seed=int(parameters.get("or_seed", 1)),
        minimum_die_size_um=(
            float(parameters["minimum_die_size_um"])
            if parameters.get("minimum_die_size_um") is not None else None
        ),
        stage_timeout_seconds=int(parameters.get("stage_timeout_seconds", 3600)),
        run_id="implementation",
        labels={"task_id": task.task_id, "design_id": task.design_id},
    )


def _plugin_result(result, *, plan_workdir: Path, workspace: Path, input_reference: dict):
    artifacts = []
    for artifact in result.artifacts:
        path = plan_workdir / artifact.path
        kind = _artifact_kind(path, artifact.kind.value)
        artifacts.append({
            "kind": kind,
            "path": str(path.resolve().relative_to(workspace)),
            "legacy_kind": artifact.kind.value,
        })
    gds = next((plan_workdir / artifact.path for artifact in result.artifacts
                if Path(artifact.path).suffix.lower() == ".gds"), None)
    if gds is not None and gds.is_file():
        try:
            from openroad_platform_visualization import render_gds
            preview = plan_workdir / "visuals/final_layout_2d.png"
            render_gds(gds, preview, dpi=150)
            artifacts.append({
                "kind": "layout_view",
                "path": str(preview.resolve().relative_to(workspace)),
                "renderer": "KLayout pya.LayoutView",
            })
        except Exception as exc:
            # The GDS remains authoritative; preview generation is optional.
            print(f"[visualization-warning] {type(exc).__name__}: {exc}", flush=True)
    run_result = plan_workdir / "run_result.json"
    artifacts.append({
        "kind": "run_result",
        "path": str(run_result.resolve().relative_to(workspace)),
    })
    snapshot_path = plan_workdir / "toolchain_snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    metrics = [
        {"name": metric.name, "value": metric.value, "unit": metric.unit,
         "context": {"source": metric.source}}
        for metric in result.metrics
    ]
    status = {
        RunStatus.SUCCEEDED: RuntimeStatus.SUCCEEDED,
        RunStatus.CANCELLED: RuntimeStatus.CANCELLED,
        RunStatus.FAILED: RuntimeStatus.FAILED,
    }[result.status]
    exit_code = 0 if status is RuntimeStatus.SUCCEEDED else 2
    failure = None if status is RuntimeStatus.SUCCEEDED else {
        "category": "orfs_cancelled" if status is RuntimeStatus.CANCELLED else "orfs_failure",
        "message": result.error or status.value,
    }
    plugin_result = PluginResult(
        status=status,
        exit_code=exit_code,
        started_at=result.started_at,
        ended_at=result.finished_at,
        metrics=tuple(metrics),
        artifacts=tuple(artifacts),
        failure=failure,
        provenance={
            "adapter": f"{ORFS_PLUGIN_ID}@{ORFS_PLUGIN_VERSION}",
            "input_rtl": dict(input_reference),
            "toolchain_snapshot": snapshot,
            "milestones": dict(result.milestones),
        },
    )
    plugin_result.validate()
    return plugin_result


def _artifact_kind(path: Path, fallback: str) -> str:
    if path.name == "toolchain_snapshot.json":
        return "toolchain_snapshot"
    if path.name == "config.mk":
        return "config"
    if path.name == "run_result.json":
        return "run_result"
    return fallback


def _optional_string(value, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Task input {name} must be a string or null")
    return value


def _write_result(path: Path, result: PluginResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
