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
import sys
from datetime import datetime, timezone
from pathlib import Path

# The script's own directory is on sys.path because Python adds it for the main
# script, so the ported modules import normally.  No sys.path surgery.
from compatibility import apply_backports, stage_flow
from toolchain import (
    ToolchainConfig,
    resolve_from_environment,
    toolchain_snapshot,
)
from config import infer_clock, infer_top, write_design_files
from digest import sha256_file
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


def design_sources(inputs: dict) -> list[Path]:
    """The design's RTL files, from either task shape.

    A task may name one file (``rtl_path``) or a rooted bundle (``rtl_files``
    with ``rtl_root``), and a reference design always uses the second.  Which
    one applies is decided here, once, so that inference, staging and the
    snapshot cannot disagree about which files are the design.

    Naming both is an error rather than a precedence rule: a caller who meant one
    of them should be told which took effect, not left to discover it.
    """
    rtl_files = tuple(
        Path(str(item)).expanduser().resolve()
        for item in (inputs.get("rtl_files") or ())
    )
    rtl_path = inputs.get("rtl_path")
    if rtl_files and rtl_path:
        raise ValueError("give either rtl_path or rtl_files, not both")
    if rtl_files:
        missing = [str(item) for item in rtl_files if not item.is_file()]
        if missing:
            raise FileNotFoundError("RTL source not found: " + ", ".join(missing))
        return list(rtl_files)
    if not rtl_path:
        raise ValueError("a task must name rtl_path or rtl_files")
    path = Path(str(rtl_path)).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"RTL file not found: {path}")
    return [path]


def write_result(path: Path, payload: dict, started_at: str) -> None:
    path.write_text(json.dumps({
        "schema_version": 2, "started_at": started_at, "ended_at": now(), **payload,
    }, indent=2), encoding="utf-8")


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

    # One profile, built once: the environment the flow runs under and the
    # snapshot that records it are the same object, so they cannot disagree.
    # Resolution lives in the toolchain module, so "an explicit path wins over
    # the environment" has one implementation rather than one per caller.
    try:
        toolchain = resolve_from_environment(
            name=str(inputs.get("toolchain_name") or "orfs"),
            orfs_root=inputs.get("orfs_root"),
            openroad_bin=inputs.get("openroad_bin"),
            yosys_bin=inputs.get("yosys_bin"),
        )
        toolchain.validate()
        flow_home = toolchain.flow_home
        cores = cores_from_environment()
    except (FileNotFoundError, ValueError) as exc:
        return fail("configuration_error", f"{type(exc).__name__}: {exc}", 3)

    try:
        flow = run_flow(
            workdir=workdir, task=task, inputs=inputs, parameters=parameters,
            flow_home=flow_home, cores=cores, toolchain=toolchain,
            cancel_requested=lambda: False,
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
    flow_home: Path, cores: int, toolchain: ToolchainConfig, cancel_requested,
) -> dict:
    """Prepare the design, run every stage, and record what happened."""
    sources = design_sources(inputs)

    platform = str(inputs.get("platform") or "nangate45")
    target_stage = str(inputs.get("target_stage") or "finish")
    if target_stage not in STAGES:
        raise ValueError(f"unsupported target stage: {target_stage!r}")

    clock_period_ns = float(inputs.get("clock_period_ns") or 10.0)
    or_seed = int(parameters.get("or_seed") or inputs.get("or_seed") or 1)
    # Inference reads the whole bundle, not one file of it: a bundle's top
    # module is often declared in one file and instantiated in another, so
    # inferring from the first file would name the wrong module -- or none.
    rtl = "\n".join(
        source.read_text(encoding="utf-8", errors="replace") for source in sources
    )
    # ``top`` is the module ORFS elaborates and the name it gives the design;
    # ``design`` is the bundle's own label and only a fallback.
    design = str(
        inputs.get("top") or inputs.get("design") or infer_top(rtl, sources[0].stem)
    )
    clock = inputs.get("clock") or infer_clock(rtl, design)

    # The bundle is the general shape; one file is the bundle with one entry, so
    # its root is the file's own directory when the task did not name one.
    rtl_root = (Path(str(inputs["rtl_root"])).expanduser().resolve()
                if inputs.get("rtl_root") else None)
    if rtl_root is None and len(sources) == 1:
        rtl_root = sources[0].parent

    report("prepare", "started")
    # Never run ``make`` in the operator's ORFS tree.  Materialize a per-attempt
    # copy first, so every write the flow performs -- Makefile outputs, patched
    # scripts, generated Tcl -- is contained by this attempt's workspace.  A run
    # that wrote into the shared tree would change the toolchain under every
    # other experiment.
    staged_flow = stage_flow(flow_home, workdir)
    report("stage-flow", "finished", status="succeeded",
           scope=str(staged_flow.name))
    backports = apply_backports(staged_flow, workdir)
    report("backport", "finished", status="succeeded",
           applied=len(backports))

    # The flow runs under the profile's environment, not under whatever the
    # adapter was started with: PATH order decides which build of a tool the
    # flow picks up, and the snapshot records this composition.
    environment = toolchain.build_environment()

    config_path = write_design_files(
        workdir=workdir, rtl_files=tuple(sources), rtl_root=rtl_root,
        design=design, platform=platform,
        clock=clock, clock_period_ns=clock_period_ns,
        rtl_include_dirs=tuple(
            Path(str(item)).expanduser().resolve()
            for item in (inputs.get("rtl_include_dirs") or ())
        ),
        synth_hdl_frontend=inputs.get("synth_hdl_frontend") or None,
        sdc_path=(Path(str(inputs["sdc_path"])).expanduser().resolve()
                  if inputs.get("sdc_path") else None),
        fast_route_tcl_path=(
            Path(str(inputs["fast_route_tcl_path"])).expanduser().resolve()
            if inputs.get("fast_route_tcl_path") else None
        ),
        design_options=dict(inputs.get("design_options") or {}),
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

    # The snapshot is what lets a stored result be attributed to a toolchain and
    # a request.  It is written before the flow runs, because a run that fails
    # still needs to say what it was failing with, and it covers the generated
    # configuration because that file is an input the flow reads.
    snapshot = toolchain_snapshot(
        toolchain, workdir=workdir,
        request={
            "platform": platform, "design": design, "target_stage": target_stage,
            "clock_period_ns": clock_period_ns, "or_seed": or_seed,
            "core_utilization_pct": parameters.get("core_utilization_pct"),
            "place_density": parameters.get("place_density"),
            "flow_parameters": dict(parameters.get("flow_parameters") or {}),
        },
        rtl_path=sources[0], rtl_files=[str(item) for item in sources],
        generated_config=config_path,
        sdc_path=(Path(str(inputs["sdc_path"]))
                  if inputs.get("sdc_path") else None),
        fast_route_tcl=(Path(str(inputs["fast_route_tcl_path"]))
                        if inputs.get("fast_route_tcl_path") else None),
    )
    (workdir / "toolchain_snapshot.json").write_text(
        json.dumps(snapshot, indent=2), encoding="utf-8")
    report("snapshot", "finished", status="succeeded",
           fingerprint=snapshot["toolchain"]["fingerprint"])

    result = FlowResult()
    run_stages = STAGES[:STAGES.index(target_stage) + 1]
    log_path = workdir / "logs" / "flow.log"
    timeout = float(inputs.get("stage_timeout_seconds") or 3600)

    for stage in run_stages:
        report(stage, "started")
        outcome, seconds = run_make(
            stage=stage, config_path=config_path, workdir=workdir,
            flow_home=staged_flow, openroad_bin=toolchain.openroad_bin,
            yosys_bin=toolchain.yosys_bin, cores=cores, environment=environment,
            timeout_seconds=timeout,
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
                flow_home=staged_flow, openroad_bin=toolchain.openroad_bin,
                yosys_bin=toolchain.yosys_bin, cores=cores,
                environment=environment, timeout_seconds=timeout,
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
