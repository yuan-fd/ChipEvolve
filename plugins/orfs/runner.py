"""Run the ORFS flow one stage at a time and gate each stage on its artifacts.

Ported from the frozen v1 runner.  The command, the stage list, the per-stage
artifact requirements and the failure-message extraction are all recorded
behaviour, not invention.

Why a stage gate at all: ``make finish`` can exit zero while leaving a stage's
database unwritten, and a run whose artifacts are missing must not be reported
as a completed flow.  The gate asks for a file of non-zero size, not for a
return code.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

#: Execution order.  ``finish`` is the signoff stage.
STAGES: tuple[str, ...] = ("synth", "floorplan", "place", "cts", "route", "finish")

#: Acceptable stage products, by stage.  Synthesis accepts either form because
#: older admitted ORFS revisions end ``make synth`` at the mapped netlist while
#: newer ones also materialize a database; requiring the database
#: unconditionally rejects a valid flow before floorplan.
STAGE_ARTIFACTS: dict[str, tuple[str, ...]] = {
    "synth": ("1_synth.odb", "1_synth.v"),
    "floorplan": ("2_floorplan.odb",),
    "place": ("3_place.odb",),
    "cts": ("4_cts.odb",),
    "route": ("5_route.odb",),
    "finish": ("6_final.odb",),
}

#: Additionally required once the flow reaches signoff.
FINISH_REQUIRED = ("6_final.def", "6_final.v", "6_final.gds")

#: The environment variable bounding parallelism, and its permitted range.
CORES_ENV = "OPENROAD_PLATFORM_ORFS_CORES"
DEFAULT_CORES = 16
MIN_CORES, MAX_CORES = 1, 64

#: Artifact kind by file suffix.  A kind is how the platform knows what a file
#: is without parsing it.
KIND_BY_SUFFIX: dict[str, str] = {
    ".v": "netlist", ".odb": "odb", ".def": "def", ".gds": "gds",
    ".log": "log", ".json": "report", ".rpt": "report", ".txt": "report",
}

#: Files worth registering as evidence, relative to the workspace root.
EVIDENCE_FILES: tuple[str, ...] = (
    "plan.json",
    "design_input_manifest.json",
    "flow_compatibility.json",
    "toolchain_snapshot.json",
    "logs/flow.log",
    "analysis/flow_error.log",
)

#: Result-tree products, relative to ``results/<platform>/<design>/base``.
RESULT_PRODUCTS: tuple[str, ...] = (
    "1_synth.odb", "1_synth.v", "2_floorplan.odb", "3_place.odb", "4_cts.odb",
    "5_route.odb", "6_final.odb", "6_final.def", "6_final.v", "6_final.gds",
)

#: Machine-readable ORFS reports.  These are artifacts, not hidden workspace
#: inputs: the platform registers and hashes them before a metric may cite them.
ROUTE_REPORTS: tuple[str, ...] = ("6_report.json", "5_2_route.json")


@dataclass
class StageResult:
    stage: str
    status: str
    returncode: int
    seconds: float
    detail: str | None = None


@dataclass
class FlowResult:
    stages: list[StageResult] = field(default_factory=list)
    gds_exported: bool = False
    failed_stage: str | None = None
    failure_message: str | None = None
    #: Explicit statements of what was and was not achieved.  They exist so a
    #: reader does not have to infer "implementation valid" from a status.
    milestones: dict[str, bool] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.failed_stage is None and all(
            s.status == "succeeded" for s in self.stages
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "stages": [
                {"stage": s.stage, "status": s.status,
                 "returncode": s.returncode, "seconds": s.seconds,
                 **({"detail": s.detail} if s.detail else {})}
                for s in self.stages
            ],
            "gds_exported": self.gds_exported,
            "failed_stage": self.failed_stage,
            "failure_message": self.failure_message,
            "milestones": dict(self.milestones),
        }


class FlowError(RuntimeError):
    """The flow could not be run at all.  Distinct from a stage failing."""


def cores_from_environment() -> int:
    """Parallelism, bounded.  An out-of-range value is an error, not a clamp.

    Clamping silently would mean the run's recorded resource usage differs from
    what the operator asked for, which makes the run hard to reason about later.
    """
    raw = os.environ.get(CORES_ENV, str(DEFAULT_CORES))
    try:
        cores = int(raw)
    except ValueError as exc:
        raise FlowError(f"{CORES_ENV} must be an integer, got {raw!r}") from exc
    if not MIN_CORES <= cores <= MAX_CORES:
        raise FlowError(
            f"{CORES_ENV} must be between {MIN_CORES} and {MAX_CORES}, got {cores}"
        )
    return cores


def make_command(*, config_path: Path, workdir: Path, flow_home: Path,
                 openroad_bin: Path, yosys_bin: Path, cores: int,
                 target: str) -> list[str]:
    """The exact ORFS invocation.

    ``EQUIVALENCE_CHECK`` and ``LEC_CHECK`` are disabled because they are not
    part of the measured protocol: enabling them would change the runtime a
    candidate is scored on and make runs incomparable.
    """
    return [
        "make",
        f"DESIGN_CONFIG={config_path}",
        f"DESIGN_HOME={workdir / 'designs'}",
        f"WORK_HOME={workdir}",
        f"OPENROAD_EXE={openroad_bin}",
        f"YOSYS_EXE={yosys_bin}",
        f"NUM_CORES={cores}",
        "EQUIVALENCE_CHECK=0",
        "LEC_CHECK=0",
        target,
    ]


def results_dir(workdir: Path, platform: str, design: str) -> Path:
    return workdir / "results" / platform / design / "base"


def reports_dir(workdir: Path, platform: str, design: str) -> Path:
    return workdir / "reports" / platform / design / "base"


def stage_gate(workdir: Path, platform: str, design: str, stage: str) -> str | None:
    """Return why a stage's products are unacceptable, or None."""
    results = results_dir(workdir, platform, design)
    alternatives = [results / name for name in STAGE_ARTIFACTS[stage]]
    missing: list[str] = []
    if not any(p.is_file() and p.stat().st_size > 0 for p in alternatives):
        missing.append("one of " + ", ".join(str(p) for p in alternatives))
    if stage == "finish":
        missing.extend(
            str(results / name) for name in FINISH_REQUIRED
            if not (results / name).is_file()
            or (results / name).stat().st_size == 0
        )
    return (f"required artifacts missing or empty: {', '.join(missing)}"
            if missing else None)


def can_export_gds(workdir: Path, platform: str, design: str) -> bool:
    """True when the flow finished but the layout was never written out."""
    results = results_dir(workdir, platform, design)
    return (not (results / "6_final.gds").is_file()
            and (results / "6_final.odb").is_file())


def failure_detail(log_path: Path) -> str | None:
    """The last real error line in the stage log, if any.

    Scans backwards for an ``[ERROR`` marker or an ``Error:`` line, because the
    useful message is at the end of the log, not the beginning.
    """
    try:
        lines = log_path.read_text(errors="replace").splitlines()
    except OSError:
        return None
    return next(
        (line.strip() for line in reversed(lines)
         if "[ERROR" in line or re.search(r"\bError:\s", line)),
        None,
    )


def run_make(
    *,
    stage: str, config_path: Path, workdir: Path, flow_home: Path,
    openroad_bin: Path, yosys_bin: Path, cores: int, timeout_seconds: float,
    cancel_requested: Callable[[], bool] | None = None,
    on_line: Callable[[], None] | None = None,
    log_path: Path,
) -> tuple[_Outcome, float]:
    """Invoke one make target.  Returns (outcome, seconds)."""
    import time

    command = make_command(
        config_path=config_path, workdir=workdir, flow_home=flow_home,
        openroad_bin=openroad_bin, yosys_bin=yosys_bin, cores=cores, target=stage,
    )
    started = time.monotonic()
    outcome = _run_guarded(
        command, cwd=flow_home, log_path=log_path,
        timeout_seconds=timeout_seconds, cancel_requested=cancel_requested,
        on_line=on_line,
    )
    return outcome, time.monotonic() - started


def stage_outcome(
    *, stage: str, outcome: "_Outcome", seconds: float,
    workdir: Path, platform: str, design: str, log_path: Path,
) -> tuple[StageResult | None, str | None]:
    """Turn a make outcome plus the stage gate into a stage result.

    The gate runs after the caller has had the chance to export a missing
    layout, because the finish stage's gate requires the layout and the export
    is a make target of its own.
    """
    if outcome.cancelled:
        return None, f"make {stage} was cancelled"
    if outcome.timed_out:
        return None, f"make {stage} exceeded its deadline"
    if outcome.returncode != 0:
        detail = failure_detail(log_path)
        message = f"make {stage} exited with {outcome.returncode}"
        if detail:
            message += f": {detail[:300]}"
        return StageResult(stage, "failed", outcome.returncode, seconds,
                           detail=(detail[:300] if detail else None)), message

    gate = stage_gate(workdir, platform, design, stage)
    if gate:
        return StageResult(stage, "failed", 0, seconds, detail=gate), gate
    return StageResult(stage, "succeeded", 0, seconds), None


def write_flow_error(workdir: Path, stage: str, message: str) -> Path:
    """Record which stage failed and why, beside the raw log.

    The evaluator and any operator read this instead of having to infer the
    failure from a truncated log tail.
    """
    path = workdir / "analysis" / "flow_error.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"stage={stage}\n{message}\n", encoding="utf-8")
    return path


def _platform_of(config_path: Path) -> str:
    """config.mk lives at designs/<platform>/<design>/config.mk."""
    return config_path.parent.parent.name


def _design_of(config_path: Path) -> str:
    return config_path.parent.name


@dataclass(frozen=True)
class _Outcome:
    returncode: int
    timed_out: bool = False
    cancelled: bool = False


def _run_guarded(
    command: Sequence[str], *, cwd: Path, log_path: Path,
    timeout_seconds: float, cancel_requested: Callable[[], bool] | None,
    on_line: Callable[[str], None] | None,
) -> _Outcome:
    """Run a command under a deadline.

    The plugin does its own supervision rather than importing the kernel's
    ProcessGuardian: a plugin is a separate program speaking a JSON protocol and
    must not depend on the platform's Python packages.  In production the
    platform's own guardian applies the outer deadline to the whole adapter, so
    this inner one only has to stop a single stage.
    """
    import time

    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with log_path.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            list(command), cwd=str(cwd), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
            start_new_session=(os.name == "posix"),
        )
        try:
            for line in process.stdout or ():
                log.write(line)
                if on_line is not None:
                    on_line(line)
                if cancel_requested is not None and cancel_requested():
                    _terminate(process)
                    return _Outcome(process.wait(), cancelled=True)
                if time.monotonic() - started >= timeout_seconds:
                    _terminate(process)
                    return _Outcome(process.wait(), timed_out=True)
        finally:
            if process.poll() is None:
                _terminate(process)
        return _Outcome(process.wait())


def _terminate(process: "subprocess.Popen[str]") -> None:
    import signal

    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        else:
            process.terminate()
    except (ProcessLookupError, PermissionError, OSError):
        process.terminate()


def collect_artifacts(
    workdir: Path, platform: str, design: str
) -> list[dict[str, Any]]:
    """Every evidence file worth registering, with its kind.

    Empty files are skipped on purpose: a clean ORFS run creates empty DRC and
    antenna reports, and an empty file is an absence marker rather than
    evidence.  The platform rejects zero-length artifacts, so registering one
    would be a protocol violation the plugin caused.
    """
    results = results_dir(workdir, platform, design)
    reports = reports_dir(workdir, platform, design)
    logs = workdir / "logs" / platform / design / "base"

    candidates: list[Path] = [workdir / name for name in EVIDENCE_FILES]
    candidates.extend(results / name for name in RESULT_PRODUCTS)
    candidates.extend(reports / name for name in (
        "6_finish.rpt", "5_route_drc.rpt", "synth_check.txt", "synth_stat.txt",
    ))
    candidates.extend(logs / name for name in ROUTE_REPORTS)
    config_dir = workdir / "designs" / platform / design
    candidates.extend(config_dir / name for name in (
        "config.mk", "constraint.sdc", "fastroute.tcl", "pdn.tcl",
    ))

    artifacts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in candidates:
        if not path.is_file() or path.stat().st_size == 0:
            continue
        try:
            store_key = str(path.resolve().relative_to(workdir.resolve()))
        except ValueError:
            continue
        if store_key in seen:
            continue
        seen.add(store_key)
        artifacts.append({
            "kind": KIND_BY_SUFFIX.get(path.suffix.lower(), "report"),
            "path": store_key,
        })
    return artifacts


def write_plan(
    workdir: Path, *, run_id: str, design: str, platform: str,
    config_path: Path, clock_period_ns: float, or_seed: int, target_stage: str,
) -> Path:
    """Record what this attempt was asked to do.

    The protected evaluator reads this file to learn the platform, design and
    clock it must score.  It is written by the adapter and treated as evidence,
    so a later reader can reconstruct the request without the task store.
    """
    path = workdir / "plan.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "run_id": run_id,
        "design": design,
        "config_path": str(config_path),
        "request": {
            "platform": platform,
            "clock_period_ns": clock_period_ns,
            "or_seed": or_seed,
            "target_stage": target_stage,
        },
    }, indent=2), encoding="utf-8")
    return path
