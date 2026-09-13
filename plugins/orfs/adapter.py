#!/usr/bin/env python3
"""Adapter entry point for the ORFS RTL-to-GDS execution capability.

Runs the flow stage by stage in the attempt workspace and reports what it
produced.  It claims nothing about quality: the platform's protected evaluator
reads the evidence this leaves behind and decides what it means.

Progress is reported on stdout using the marker the manifest declares, so the
platform can show stage timing without knowing any ORFS stage name.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# The script's own directory is on sys.path because Python adds it for the main
# script, so the ported modules import normally.  No sys.path surgery.
from config import infer_clock, infer_top, write_design_files
from runner import (
    STAGES,
    FlowResult,
    can_export_gds,
    collect_artifacts,
    cores_from_environment,
    results_dir as results_of,
    run_make,
    stage_outcome,
    write_flow_error,
    write_plan,
)

PROGRESS_MARKER = "[progress]"
PLAN_SCHEMA_VERSION = 1


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def report(stage: str, phase: str, **extra) -> None:
    print(PROGRESS_MARKER + " " + json.dumps(
        {"stage": stage, "phase": phase, **extra}
    ), flush=True)


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_result(path: Path, payload: dict, started_at: str) -> None:
    path.write_text(json.dumps({
        "schema_version": 2, "started_at": started_at, "ended_at": now(), **payload,
    }, indent=2), encoding="utf-8")


def resolve_toolchain(task: dict) -> tuple[Path, Path, Path]:
    """Locate ORFS, OpenROAD and Yosys.

    These are configuration, not secrets: they come from the task, falling back
    to the environment.  A missing tool is an error the platform reports, never
    a reason to skip a stage quietly.
    """
    inputs = task.get("inputs") or {}
    flow_home = Path(
        inputs.get("flow_home") or os.environ.get("ORFS_FLOW_HOME", "")
    ).expanduser()
    openroad_bin = Path(
        inputs.get("openroad_bin") or os.environ.get("OPENROAD_EXE", "")
    ).expanduser()
    yosys_bin = Path(
        inputs.get("yosys_bin") or os.environ.get("YOSYS_EXE", "")
    ).expanduser()

    if not (flow_home / "Makefile").is_file():
        raise FileNotFoundError(f"ORFS Makefile not found under {flow_home}")
    for name, binary in (("OpenROAD", openroad_bin), ("Yosys", yosys_bin)):
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise FileNotFoundError(f"{name} executable not found: {binary}")
    return flow_home, openroad_bin, yosys_bin


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()

    started_at = now()
    request_path = Path(args.request)
    result_path = Path(args.result)
    workdir = result_path.parent

    request = json.loads(request_path.read_text(encoding="utf-8"))
    task = request["task"]
    inputs = task.get("inputs") or {}
    parameters = task.get("parameters") or {}

    def fail(category: str, message: str, exit_code: int = 1) -> int:
        write_result(result_path, {
            "status": "failed", "exit_code": exit_code,
            "failure": {"category": category, "message": message,
                        "retryable": category in {"tool_error", "timeout"}},
            "artifacts": [],
        }, started_at)
        return exit_code

    try:
        flow_home, openroad_bin, yosys_bin = resolve_toolchain(task)
        cores = cores_from_environment()
    except (FileNotFoundError, ValueError) as exc:
        return fail("configuration_error", f"{type(exc).__name__}: {exc}", 3)

    try:
        flow = run_flow(
            workdir=workdir, task=task, inputs=inputs, parameters=parameters,
            flow_home=flow_home, openroad_bin=openroad_bin, yosys_bin=yosys_bin,
            cores=cores, cancel_requested=lambda: False,
        )
    except Exception as exc:  # noqa: BLE001 - reported with its type
        return fail("tool_error", f"{type(exc).__name__}: {exc}")

    artifacts = collect_artifacts(
        workdir, flow["platform"], flow["design"]
    ) if flow["platform"] and flow["design"] else []

    if not flow["result"].succeeded:
        # Partial evidence is still evidence: the platform registers what the
        # attempt produced even though the run failed.
        write_result(result_path, {
            "status": "failed",
            "exit_code": 1,
            "failure": {
                "category": "flow_error",
                "message": flow["result"].failure_message or "the flow did not complete",
                "stage": flow["result"].failed_stage,
            },
            "artifacts": artifacts,
        }, started_at)
        return 1

    write_result(result_path, {
        "status": "succeeded",
        "exit_code": 0,
        "artifacts": artifacts,
        "metrics": [],
    }, started_at)
    return 0


def run_flow(
    *, workdir: Path, task: dict, inputs: dict, parameters: dict,
    flow_home: Path, openroad_bin: Path, yosys_bin: Path, cores: int,
    cancel_requested,
) -> dict:
    """Prepare the design, run every stage, and record what happened."""
    rtl_path = Path(str(inputs["rtl_path"])).expanduser().resolve()
    if not rtl_path.is_file():
        raise FileNotFoundError(f"RTL file not found: {rtl_path}")

    platform = str(inputs.get("platform") or "nangate45")
    target_stage = str(inputs.get("target_stage") or "finish")
    if target_stage not in STAGES:
        raise ValueError(f"unsupported target stage: {target_stage!r}")

    clock_period_ns = float(inputs.get("clock_period_ns") or 10.0)
    or_seed = int(parameters.get("or_seed") or inputs.get("or_seed") or 1)
    rtl = rtl_path.read_text(encoding="utf-8", errors="replace")
    design = str(inputs.get("design") or infer_top(rtl, rtl_path.stem))
    clock = inputs.get("clock") or infer_clock(rtl, design)

    report("prepare", "started")
    config_path = write_design_files(
        workdir=workdir, rtl_path=rtl_path, design=design, platform=platform,
        clock=clock, clock_period_ns=clock_period_ns,
        core_utilization_pct=float(
            parameters.get("core_utilization_pct")
            or inputs.get("core_utilization_pct") or 40.0
        ),
        place_density=float(
            parameters.get("place_density") or inputs.get("place_density") or 0.6
        ),
        or_seed=or_seed,
        minimum_die_size_um=(
            float(parameters["minimum_die_size_um"])
            if parameters.get("minimum_die_size_um") is not None else None
        ),
        flow_parameters=dict(parameters.get("flow_parameters") or {}),
    )
    write_plan(
        workdir, run_id=str(task["task_id"]), design=design, platform=platform,
        config_path=config_path, clock_period_ns=clock_period_ns,
        or_seed=or_seed, target_stage=target_stage,
    )
    report("prepare", "finished", status="succeeded")

    result = FlowResult()
    run_stages = STAGES[:STAGES.index(target_stage) + 1]
    log_path = workdir / "logs" / "flow.log"
    timeout = float(inputs.get("stage_timeout_seconds") or 3600)

    for stage in run_stages:
        report(stage, "started")
        outcome, seconds = run_make(
            stage=stage, config_path=config_path, workdir=workdir,
            flow_home=flow_home, openroad_bin=openroad_bin, yosys_bin=yosys_bin,
            cores=cores, timeout_seconds=timeout,
            cancel_requested=cancel_requested, on_line=None, log_path=log_path,
        )

        # A missing layout is exported as part of this stage, *before* the gate
        # is evaluated -- the finish gate requires the layout, and the export is
        # a make target of its own.  Gating first would deadlock: the gate would
        # fail, the run would stop, and the export would never be attempted.
        if stage == "finish" and can_export_gds(workdir, platform, design):
            report("gds", "started")
            gds_outcome, gds_seconds = run_make(
                stage="gds", config_path=config_path, workdir=workdir,
                flow_home=flow_home, openroad_bin=openroad_bin,
                yosys_bin=yosys_bin, cores=cores, timeout_seconds=timeout,
                cancel_requested=cancel_requested, on_line=None,
                log_path=log_path,
            )
            result.gds_exported = (
                gds_outcome.returncode == 0 and not gds_outcome.timed_out
                and not gds_outcome.cancelled
            )
            report("gds", "finished", seconds=gds_seconds,
                   status="succeeded" if result.gds_exported else "failed")

        stage_result, failure = stage_outcome(
            stage=stage, outcome=outcome, seconds=seconds, workdir=workdir,
            platform=platform, design=design, log_path=log_path,
        )
        if stage_result is None:
            report(stage, "finished", status="failed")
            result.failed_stage = stage
            result.failure_message = failure
            write_flow_error(workdir, stage, failure or "failed")
            break
        result.stages.append(stage_result)
        report(stage, "finished", status=stage_result.status,
               seconds=stage_result.seconds)
        if stage_result.status != "succeeded":
            result.failed_stage = stage
            result.failure_message = failure
            write_flow_error(workdir, stage, failure or stage_result.status)
            break

    completed = {s.stage for s in result.stages if s.status == "succeeded"}
    layout = results_of(workdir, platform, design) / "6_final.gds"
    result.milestones = {
        "synthesizable": "synth" in completed,
        # The platform never claims functional verification: that is a
        # separate capability with its own evidence, and asserting it here
        # would turn "it synthesized" into "it works".
        "functionally_verified": False,
        "implementation_valid": (
            target_stage == "finish" and result.succeeded
        ),
        "gds_complete": layout.is_file() and layout.stat().st_size > 0,
    }
    (workdir / "run_result.json").write_text(
        json.dumps(result.to_dict(), indent=2), encoding="utf-8"
    )
    return {"result": result, "platform": platform, "design": design}


if __name__ == "__main__":
    sys.exit(main())
