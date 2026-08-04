from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from openroad_platform_contracts import (
    Artifact,
    ArtifactKind,
    ExecutionPlan,
    Metric,
    RunRequest,
    RunResult,
    RunStage,
    RunStatus,
    StageResult,
)

from .orfs_config import infer_clock, infer_top, write_design_files
from .process_guardian import ProcessGuardian


STAGE_ARTIFACT = {
    RunStage.SYNTH: "1_synth.odb",
    RunStage.FLOORPLAN: "2_floorplan.odb",
    RunStage.PLACE: "3_place.odb",
    RunStage.CTS: "4_cts.odb",
    RunStage.ROUTE: "5_route.odb",
    RunStage.FINISH: "6_final.odb",
}


class ORFSRunner:
    def __init__(
        self,
        *,
        orfs_root: str | Path,
        work_root: str | Path,
        openroad_bin: str | Path | None = None,
        yosys_bin: str | Path | None = None,
        guardian: ProcessGuardian | None = None,
    ):
        self.orfs_root = Path(orfs_root).expanduser().resolve()
        self.flow_home = self.orfs_root / "flow"
        self.work_root = Path(work_root).expanduser().resolve()
        self.openroad_bin = Path(openroad_bin or Path.home() / "bin/openroad").expanduser().resolve()
        self.yosys_bin = Path(yosys_bin or Path.home() / "bin/yosys").expanduser().resolve()
        self.guardian = guardian or ProcessGuardian()

    def prepare(self, request: RunRequest) -> ExecutionPlan:
        request.validate()
        self._validate_runtime()
        rtl_path = Path(request.rtl_path).expanduser().resolve()
        rtl = rtl_path.read_text(encoding="utf-8", errors="replace")
        design = request.top or infer_top(rtl, rtl_path.stem)
        if not re.fullmatch(r"[A-Za-z_]\w*", design):
            raise ValueError(f"Invalid inferred top module: {design}")
        clock = request.clock or infer_clock(rtl, design)
        workdir = self.work_root / request.run_id
        if workdir.exists() and any(workdir.iterdir()):
            raise FileExistsError(f"Run workspace is not empty: {workdir}")
        workdir.mkdir(parents=True, exist_ok=True)
        config_path = write_design_files(
            workdir=workdir,
            rtl_path=rtl_path,
            design=design,
            platform=request.platform,
            clock=clock,
            clock_period_ns=request.clock_period_ns,
            core_utilization_pct=request.core_utilization_pct,
            place_density=request.place_density,
        )
        stages = tuple(stage for stage in RunStage
                       if list(RunStage).index(stage) <= list(RunStage).index(request.target_stage))
        plan = ExecutionPlan(
            run_id=request.run_id,
            design=design,
            clock=clock,
            workdir=str(workdir),
            flow_home=str(self.flow_home),
            config_path=str(config_path),
            stages=stages,
            request=request,
        )
        self._write_json(workdir / "plan.json", {
            "schema_version": 1,
            "run_id": plan.run_id,
            "design": plan.design,
            "clock": plan.clock,
            "workdir": plan.workdir,
            "flow_home": plan.flow_home,
            "config_path": plan.config_path,
            "stages": [stage.value for stage in plan.stages],
            "request": request.to_dict(),
            "tools": self.tool_versions(),
        })
        return plan

    def run(
        self,
        plan: ExecutionPlan,
        *,
        cancel_requested: Callable[[], bool] | None = None,
        on_line: Callable[[str], None] | None = None,
        on_stage: Callable[[StageResult], None] | None = None,
    ) -> RunResult:
        workdir = Path(plan.workdir)
        log_path = workdir / "logs" / "flow.log"
        started = datetime.now(timezone.utc)
        stage_results: list[StageResult] = []
        final_status = RunStatus.SUCCEEDED
        error = None

        for stage in plan.stages:
            command = self._command(plan, stage)
            outcome = self.guardian.run(
                command,
                cwd=self.flow_home,
                env=self._environment(),
                log_path=log_path,
                timeout_seconds=plan.request.stage_timeout_seconds,
                cancel_requested=cancel_requested,
                on_line=on_line,
            )
            gds_exported = False
            if stage == RunStage.FINISH and self._can_export_gds(plan):
                gds_exported = self._export_gds(
                    plan,
                    cancel_requested=cancel_requested,
                    on_line=on_line,
                )
            artifact_error = self._stage_gate(plan, stage)
            if outcome.cancelled:
                status = RunStatus.CANCELLED
                message = "Cancellation requested"
            elif outcome.timed_out:
                status = RunStatus.FAILED
                message = f"Stage exceeded {plan.request.stage_timeout_seconds}s timeout"
            elif outcome.returncode != 0:
                status = RunStatus.FAILED
                message = self._process_failure_message(
                    log_path, stage, outcome.returncode, gds_exported=gds_exported
                )
            elif artifact_error:
                status = RunStatus.FAILED
                message = artifact_error
            else:
                status = RunStatus.SUCCEEDED
                message = None
            stage_result = StageResult(
                stage=stage,
                status=status,
                returncode=outcome.returncode,
                seconds=round(outcome.seconds, 3),
                message=message,
            )
            stage_results.append(stage_result)
            if on_stage is not None:
                on_stage(stage_result)
            if status != RunStatus.SUCCEEDED:
                final_status = status
                error = message
                self._write_flow_error(workdir, stage, message or status.value)
                break

        self._run_analysis(plan, stage_results)
        artifacts = self._collect_artifacts(plan)
        metrics = self._collect_metrics(plan)
        completed_stages = {item.stage for item in stage_results
                            if item.status == RunStatus.SUCCEEDED}
        gds_path = self._results_dir(plan) / "6_final.gds"
        result = RunResult(
            run_id=plan.run_id,
            status=final_status,
            design=plan.design,
            workdir=plan.workdir,
            started_at=started.isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat(),
            stages=tuple(stage_results),
            artifacts=tuple(artifacts),
            milestones={
                "synthesizable": RunStage.SYNTH in completed_stages,
                "functionally_verified": False,
                "implementation_valid": (
                    plan.request.target_stage == RunStage.FINISH and
                    final_status == RunStatus.SUCCEEDED
                ),
                "gds_complete": gds_path.is_file() and gds_path.stat().st_size > 0,
            },
            metrics=tuple(metrics),
            error=error,
        )
        self._write_json(workdir / "run_result.json", result.to_dict())
        return result

    def tool_versions(self) -> dict[str, str | None]:
        return {
            "openroad": self._version([str(self.openroad_bin), "-version"]),
            "yosys": self._version([str(self.yosys_bin), "-V"]),
            "orfs_commit": self._version(["git", "-C", str(self.orfs_root), "rev-parse", "HEAD"]),
        }

    def _validate_runtime(self) -> None:
        if not (self.flow_home / "Makefile").is_file():
            raise FileNotFoundError(f"ORFS Makefile not found: {self.flow_home / 'Makefile'}")
        for name, binary in (("OpenROAD", self.openroad_bin), ("Yosys", self.yosys_bin)):
            if not binary.is_file() or not os.access(binary, os.X_OK):
                raise FileNotFoundError(f"{name} executable not found: {binary}")

    def _command(self, plan: ExecutionPlan, stage: RunStage) -> list[str]:
        return self._make_command(plan, stage.value)

    def _make_command(self, plan: ExecutionPlan, target: str) -> list[str]:
        workdir = Path(plan.workdir)
        return [
            "make",
            f"DESIGN_CONFIG={plan.config_path}",
            f"DESIGN_HOME={workdir / 'designs'}",
            f"WORK_HOME={workdir}",
            f"OPENROAD_EXE={self.openroad_bin}",
            f"YOSYS_EXE={self.yosys_bin}",
            "EQUIVALENCE_CHECK=0",
            "LEC_CHECK=0",
            target,
        ]

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PATH"] = os.pathsep.join([
            str(self.openroad_bin.parent),
            str(self.yosys_bin.parent),
            str(Path.home() / ".local/bin"),
            env.get("PATH", ""),
        ])
        return env

    @staticmethod
    def _results_dir(plan: ExecutionPlan) -> Path:
        return (Path(plan.workdir) / "results" / plan.request.platform /
                plan.design / "base")

    def _stage_gate(self, plan: ExecutionPlan, stage: RunStage) -> str | None:
        results = self._results_dir(plan)
        required = [results / STAGE_ARTIFACT[stage]]
        if stage == RunStage.FINISH:
            required.extend(results / name for name in ("6_final.def", "6_final.v", "6_final.gds"))
        missing = [str(path) for path in required if not path.is_file() or path.stat().st_size == 0]
        return f"Required artifacts missing or empty: {', '.join(missing)}" if missing else None

    def _can_export_gds(self, plan: ExecutionPlan) -> bool:
        results = self._results_dir(plan)
        return not (results / "6_final.gds").is_file() and (results / "6_final.odb").is_file()

    def _export_gds(
        self,
        plan: ExecutionPlan,
        *,
        cancel_requested: Callable[[], bool] | None,
        on_line: Callable[[str], None] | None,
    ) -> bool:
        results = self._results_dir(plan)
        outcome = self.guardian.run(
            self._make_command(plan, "gds"),
            cwd=self.flow_home,
            env=self._environment(),
            log_path=Path(plan.workdir) / "logs" / "flow.log",
            timeout_seconds=plan.request.stage_timeout_seconds,
            cancel_requested=cancel_requested,
            on_line=on_line,
        )
        gds = results / "6_final.gds"
        return outcome.returncode == 0 and not outcome.timed_out and not outcome.cancelled \
            and gds.is_file() and gds.stat().st_size > 0

    @staticmethod
    def _process_failure_message(
        log_path: Path,
        stage: RunStage,
        returncode: int,
        *,
        gds_exported: bool,
    ) -> str:
        detail = None
        try:
            lines = log_path.read_text(errors="replace").splitlines()
            detail = next(
                (line.strip() for line in reversed(lines)
                 if "[ERROR" in line or re.search(r"\bError:\s", line)),
                None,
            )
        except OSError:
            pass
        message = f"make {stage.value} exited with {returncode}"
        if detail:
            message += f": {detail[:300]}"
        if gds_exported:
            message += "; GDS export succeeded, but implementation validity failed"
        return message

    def _collect_artifacts(self, plan: ExecutionPlan) -> list[Artifact]:
        workdir = Path(plan.workdir)
        candidates = [
            workdir / "plan.json",
            workdir / "logs/flow.log",
            workdir / "analysis/report.json",
            workdir / "analysis/flow_error.log",
        ]
        results = self._results_dir(plan)
        candidates.extend(results / name for name in (
            "1_synth.odb", "2_floorplan.odb", "3_place.odb", "4_cts.odb",
            "5_route.odb", "6_final.odb", "6_final.def", "6_final.v", "6_final.gds",
        ))
        suffix_kinds = {
            ".v": ArtifactKind.NETLIST,
            ".odb": ArtifactKind.ODB,
            ".def": ArtifactKind.DEF,
            ".gds": ArtifactKind.GDS,
            ".log": ArtifactKind.LOG,
            ".json": ArtifactKind.REPORT,
        }
        artifacts = []
        for path in candidates:
            if not path.is_file():
                continue
            artifacts.append(Artifact(
                kind=suffix_kinds.get(path.suffix.lower(), ArtifactKind.OTHER),
                path=str(path.relative_to(workdir)),
                size_bytes=path.stat().st_size,
                sha256=self._sha256(path),
            ))
        return artifacts

    @staticmethod
    def _write_flow_error(workdir: Path, stage: RunStage, message: str) -> None:
        path = workdir / "analysis" / "flow_error.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"stage={stage.value}\nreason={message}\n", encoding="utf-8")

    @staticmethod
    def _run_analysis(plan: ExecutionPlan, stages: list[StageResult]) -> None:
        try:
            from openroad_platform_analysis.pipeline import analyze_run
        except ImportError:
            return
        runtime = sum(item.seconds for item in stages)
        try:
            analyze_run(
                plan.workdir,
                platform=plan.request.platform,
                design=plan.design,
                runtime_seconds=runtime,
                expected_stage=plan.request.target_stage.value,
            )
        except Exception as exc:
            path = Path(plan.workdir) / "analysis" / "analysis_error.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")

    def _collect_metrics(self, plan: ExecutionPlan) -> list[Metric]:
        try:
            from openroad_platform_analysis.parsers.stage_json import extract_metrics
        except ImportError:
            return []
        payload = extract_metrics(
            Path(plan.workdir), plan.request.platform, plan.design,
            expected_stage=plan.request.target_stage.value,
        )
        summary = payload.get("summary", {})
        metrics = []
        for key, value in summary.items():
            if isinstance(value, (str, int, float)) or value is None:
                metrics.append(Metric(name=key, value=value, source="ORFS stage JSON"))
        return metrics

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _version(command: list[str]) -> str | None:
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return next((line.strip() for line in result.stdout.splitlines() if line.strip()), None)
