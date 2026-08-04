"""Generate machine-readable gray-box evidence from one native ORFS run."""

from __future__ import annotations

import hashlib
import json
import os
import platform as host_platform
import re
import shlex
from datetime import datetime, timedelta
from pathlib import Path

from openroad_platform_analysis.graybox_models import (
    SCHEMA_VERSION, ArtifactRecord, EvaluationGate, LogEvent, ParameterRecord,
    SubstageRecord, record_dict,
)
from openroad_platform_analysis.parsers import stage_json


PLACE_SUBSTAGES = (
    {"id": "3_1_place_gp_skip_io", "name": "Global placement without fixed IO",
     "script": "global_place_skip_io.tcl", "inputs": ["2_floorplan.odb", "2_floorplan.sdc"],
     "outputs": ["3_1_place_gp_skip_io.odb"], "commands": ("global_placement",)},
    {"id": "3_2_place_iop", "name": "IO placement",
     "script": "io_placement.tcl", "inputs": ["3_1_place_gp_skip_io.odb"],
     "outputs": ["3_2_place_iop.odb", "3_2_place_iop.tcl"], "commands": ("place_pins", "exec cp")},
    {"id": "3_3_place_gp", "name": "Timing/routability driven global placement",
     "script": "global_place.tcl", "inputs": ["3_2_place_iop.odb", "2_floorplan.sdc"],
     "outputs": ["3_3_place_gp.odb"], "commands": ("global_placement",)},
    {"id": "3_4_place_resized", "name": "Placement sizing and buffering",
     "script": "resize.tcl", "inputs": ["3_3_place_gp.odb", "2_floorplan.sdc"],
     "outputs": ["3_4_place_resized.odb"], "commands": ("repair_design", "global_placement -incremental")},
    {"id": "3_5_place_dp", "name": "Detailed placement and legalization",
     "script": "detail_place.tcl", "inputs": ["3_4_place_resized.odb"],
     "outputs": ["3_5_place_dp.odb"], "commands": ("detailed_placement", "check_placement")},
    {"id": "3_6_place_repair_timing", "name": "Optional post-place timing repair",
     "script": "repair_timing_post_place.tcl", "inputs": ["3_5_place_dp.odb", "3_place.sdc"],
     "outputs": ["3_6_place_repair_timing.odb"], "commands": ("repair_timing", "detailed_placement")},
)


STAGE_SUBSTAGES = {
    "synth": (
        {"id": "1_1_yosys_canonicalize", "name": "RTL canonicalization",
         "script": "flow/scripts/synth_canonicalize.tcl", "inputs": [],
         "outputs": ["1_1_yosys_canonicalize.rtlil"],
         "commands": ("read_verilog", "hierarchy", "write_rtlil")},
        {"id": "1_2_yosys", "name": "Yosys logic synthesis and technology mapping",
         "script": "flow/scripts/synth.tcl", "inputs": ["1_1_yosys_canonicalize.rtlil"],
         "outputs": ["1_2_yosys.v", "1_2_yosys.sdc"],
         "commands": ("synth", "abc", "write_verilog")},
        {"id": "1_synth", "name": "Import synthesized netlist into OpenDB",
         "script": "flow/scripts/synth_odb.tcl", "inputs": ["1_2_yosys.v", "1_2_yosys.sdc"],
         "outputs": ["1_synth.odb", "1_synth.sdc"],
         "commands": ("load_design", "orfs_write_db", "orfs_write_sdc")},
    ),
    "floorplan": (
        {"id": "2_1_floorplan", "name": "Die/core initialization and floorplan setup",
         "script": "flow/scripts/floorplan.tcl", "inputs": ["1_synth.odb", "1_synth.sdc"],
         "outputs": ["2_1_floorplan.odb", "2_1_floorplan.sdc"],
         "commands": ("initialize_floorplan", "make_tracks", "repair_timing")},
        {"id": "2_2_floorplan_macro", "name": "Macro placement",
         "script": "flow/scripts/macro_place.tcl", "inputs": ["2_1_floorplan.odb", "2_1_floorplan.sdc"],
         "outputs": ["2_2_floorplan_macro.odb", "2_2_floorplan_macro.tcl"],
         "completion_outputs": ["2_2_floorplan_macro.odb"],
         "commands": ("macro_placement", "write_macro_placement"),
         "skip_patterns": ("No macros found: Skipping macro_placement",)},
        {"id": "2_3_floorplan_tapcell", "name": "Tapcell and well-tie insertion",
         "script": "flow/scripts/tapcell.tcl", "inputs": ["2_2_floorplan_macro.odb"],
         "outputs": ["2_3_floorplan_tapcell.odb"],
         "commands": ("tapcell", "source $::env(TAPCELL_TCL)")},
        {"id": "2_4_floorplan_pdn", "name": "Power distribution network generation",
         "script": "flow/scripts/pdn.tcl", "inputs": ["2_3_floorplan_tapcell.odb"],
         "outputs": ["2_4_floorplan_pdn.odb", "2_floorplan.odb", "2_floorplan.sdc"],
         "completion_outputs": ["2_4_floorplan_pdn.odb"],
         "commands": ("pdngen",)},
    ),
    "place": tuple({**item, "script": f"flow/scripts/{item['script']}"}
                   for item in PLACE_SUBSTAGES),
    "cts": (
        {"id": "4_1_cts", "name": "Clock tree synthesis and post-CTS repair",
         "script": "flow/scripts/cts.tcl", "inputs": ["3_place.odb", "3_place.sdc"],
         "outputs": ["4_1_cts.odb", "4_cts.odb", "4_cts.sdc"],
         "completion_outputs": ["4_1_cts.odb", "4_cts.sdc"],
         "commands": ("clock_tree_synthesis", "detailed_placement", "repair_timing")},
    ),
    "route": (
        {"id": "5_1_grt", "name": "Global routing and routability repair",
         "script": "flow/scripts/global_route.tcl", "inputs": ["4_cts.odb", "4_cts.sdc"],
         "outputs": ["5_1_grt.odb", "5_1_grt.sdc", "route.guide"],
         "completion_outputs": ["5_1_grt.odb"],
         "commands": ("global_route",), "report_globs": ("congestion*.rpt",)},
        {"id": "5_2_route", "name": "Detailed routing and antenna checks",
         "script": "flow/scripts/detail_route.tcl", "inputs": ["5_1_grt.odb", "5_1_grt.sdc"],
         "outputs": ["5_2_route.odb", "maze.log"],
         "completion_outputs": ["5_2_route.odb"],
         "commands": ("detailed_route", "check_antennas"),
         "report_globs": ("5_route_drc.rpt", "drt_antennas.log")},
        {"id": "5_3_fillcell", "name": "Filler-cell insertion",
         "script": "flow/scripts/fillcell.tcl", "inputs": ["5_2_route.odb"],
         "outputs": ["5_3_fillcell.odb", "5_route.odb", "5_route.sdc"],
         "completion_outputs": ["5_3_fillcell.odb"],
         "commands": ("filler_placement",),
         "skip_patterns": ("No fill cells", "Skipping filler")},
    ),
    "finish": (
        {"id": "6_1_fill", "name": "Optional density fill",
         "script": "flow/scripts/density_fill.tcl", "inputs": ["5_route.odb", "5_route.sdc"],
         "outputs": ["6_1_fill.odb", "6_1_fill.sdc"],
         "commands": ("density_fill", "orfs_write_db"),
         "skip_patterns": ("exec cp",)},
        {"id": "6_report", "name": "Final extraction, timing and signoff report",
         "script": "flow/scripts/final_report.tcl", "inputs": ["6_1_fill.odb", "6_1_fill.sdc"],
         "outputs": ["6_final.odb", "6_final.def", "6_final.v", "6_final.spef", "6_final.sdf"],
         "completion_outputs": ["6_final.odb", "6_final.def", "6_final.v"],
         "commands": ("extract_parasitics", "write_spef", "write_def", "write_verilog"),
         "report_globs": ("6_*.rpt", "VDD.rpt", "VSS.rpt")},
        {"id": "6_1_merge", "name": "DEF-to-GDS stream merge with KLayout",
         "script": "flow/util/def2stream.py", "inputs": ["6_final.def"],
         "outputs": ["6_1_merged.gds", "6_final.gds"],
         "completion_outputs": ["6_final.gds"], "commands": ("def2stream.py",),
         "command_source": "flow/Makefile", "make_target": "do-gds-merged"},
    ),
}


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def _source_ref(path: Path, pattern: str, root: Path, kind: str = "source") -> dict:
    reference = {"path": _relative(path, root), "kind": kind, "line": None,
                 "excerpt": None, "sha256": sha256(path), "confidence": "unconfirmed"}
    if not path.is_file():
        return reference
    regex = re.compile(pattern)
    for number, line in enumerate(path.read_text(errors="replace").splitlines(), start=1):
        if regex.search(line):
            reference.update(line=number, excerpt=line.strip(), confidence="source_confirmed")
            break
    return reference


def _log_ref(path: Path, pattern: str, workdir: Path) -> dict:
    ref = _source_ref(path, pattern, workdir, "runtime_log")
    if ref["line"]:
        ref["confidence"] = "runtime_confirmed"
    return ref


def _parameter_records(workdir: Path, project_root: Path, orfs_root: Path,
                       platform: str, design: str, requested: dict,
                       effective: dict) -> list[dict]:
    config = workdir / "designs" / platform / design / "config.mk"
    gp_log = workdir / "logs" / platform / design / "base" / "3_3_place_gp.log"
    makefile = orfs_root / "flow" / "Makefile"
    scripts = orfs_root / "flow" / "scripts"
    contracts = project_root / "packages" / "contracts" / "src" / "openroad_platform_contracts" / "models.py"
    runner = project_root / "packages" / "execution" / "src" / "openroad_platform_execution" / "orfs_runner.py"

    place_evidence = [
        _source_ref(contracts, r'place_density:\s*float', project_root),
        _source_ref(runner, r'place_density=request\.place_density', project_root),
        _source_ref(config, r'^export PLACE_DENSITY\s*=', workdir, "generated_config"),
        _source_ref(makefile, r'do-step,3_3_place_gp', orfs_root),
        _source_ref(scripts / "util.tcl", r'set place_density \$::env\(PLACE_DENSITY\)', orfs_root),
        _source_ref(scripts / "global_place.tcl", r'global_placement \{\*\}\$all_args', orfs_root),
        _log_ref(gp_log, r'global_placement -density', workdir),
        _log_ref(gp_log, r'Placement target density:', workdir),
    ]
    place_confirmed = any(ref["confidence"] == "runtime_confirmed" for ref in place_evidence)
    parameters = [ParameterRecord(
        display_name="放置密度", web_field="place-density", internal_name="place_density",
        orfs_name="PLACE_DENSITY", default=0.45,
        value=effective.get("openroad_place_density", effective.get("place_density")),
        data_type="float", allowed={"minimum_exclusive": 0, "maximum": 1}, unit="ratio",
        platforms=["nangate45"], stage="place", substage="3_3_place_gp",
        engineering_definition="Global placement target density passed to OpenROAD.",
        plain_explanation="像规定城区允许有多拥挤；过高会挤压布线空间，过低会浪费面积。",
        affected_metrics=["estimated_wirelength_um", "congestion_overflow", "setup_wns_ns", "drc_errors"],
        risks=["值低于最小可行密度时会被物理约束限制", "过高可能造成拥塞或不可合法化"],
        confidence="runtime_confirmed" if place_confirmed else "source_confirmed",
        chain=[
            {"layer": "contract", "value": requested.get("place_density"), "field": "RunRequest.place_density"},
            {"layer": "runner", "value": requested.get("place_density"), "field": "ORFSRunner.prepare"},
            {"layer": "orfs_config", "value": effective.get("place_density"), "name": "PLACE_DENSITY"},
            {"layer": "tcl", "value": effective.get("openroad_place_density"),
             "command": "global_placement -density"},
            {"layer": "checkpoint", "value": "3_3_place_gp.odb"},
        ], evidence=place_evidence,
    )]
    basic = (
        ("核心利用率", "core-utilization", "core_utilization_pct", "CORE_UTILIZATION", 10.0,
         "percent", "floorplan", "控制初始 core 面积目标。", "决定芯片核心区域预留多少空白。"),
        ("时钟周期", "period", "clock_period_ns", "CLOCK_PERIOD", 10.0,
         "ns", "synth", "Target clock period used by timing constraints.", "周期越短，要求电路跑得越快。"),
    )
    for display, web, internal, orfs, default, unit, stage, definition, plain in basic:
        config_ref = _source_ref(config, rf'^export {orfs}\s*=', workdir, "generated_config")
        parameters.append(record_dict(ParameterRecord(
            display_name=display, web_field=web, internal_name=internal, orfs_name=orfs,
            default=default, value=effective.get(internal), data_type="float", allowed=None,
            unit=unit, platforms=[platform], stage=stage, substage=None,
            engineering_definition=definition, plain_explanation=plain,
            affected_metrics=["area_um2", "setup_wns_ns", "runtime_seconds"],
            risks=["需要结合目标工艺和设计规模解释"],
            confidence="source_confirmed" if config_ref["line"] else "unconfirmed",
            chain=[{"layer": "orfs_config", "name": orfs, "value": effective.get(internal)}],
            evidence=[config_ref],
        )))
    return [record_dict(parameters[0]), *parameters[1:]]


def _substage_metrics(json_path: Path, workdir: Path, stage: str) -> tuple[dict, dict]:
    try:
        raw = json.loads(json_path.read_text(errors="replace"))
    except (OSError, ValueError):
        return {}, {}
    metrics, sources = {}, {}
    for name, candidates in stage_json.METRIC_SPECS.get(stage, []):
        value = stage_json._pick(raw, candidates)
        if value is None:
            continue
        if name in stage_json.PCT_METRICS and value <= 1:
            value *= 100
        metrics[name] = round(value, 6) if isinstance(value, float) else value
        for raw_key in raw:
            stripped = stage_json._strip_ns(raw_key)
            if any(stripped == candidate or stripped.endswith(candidate) for candidate in candidates):
                sources[name] = {"file": _relative(json_path, workdir), "raw_key": raw_key,
                                 "method": "orfs_json", "confidence": "runtime_confirmed"}
                break
    return metrics, sources


def _runtime_from_log(path: Path) -> float | None:
    if not path.is_file():
        return None
    matches = re.findall(r'Elapsed time:\s*(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)', path.read_text(errors="replace"))
    if not matches:
        return None
    hours, minutes, seconds = matches[-1]
    return round(int(hours or 0) * 3600 + int(minutes) * 60 + float(seconds), 3)


def _flow_substages(workdir: Path, orfs_root: Path, platform: str,
                    design: str) -> dict[str, list[dict]]:
    result_dir = workdir / "results" / platform / design / "base"
    log_dir = workdir / "logs" / platform / design / "base"
    report_dir = workdir / "reports" / platform / design / "base"
    stages = {}
    for stage, definitions in STAGE_SUBSTAGES.items():
        rows = []
        for definition in definitions:
            prefix = definition["id"]
            log = log_dir / f"{prefix}.log"
            metric_json = log_dir / f"{prefix}.json"
            outputs = [result_dir / name for name in definition["outputs"]]
            completion_names = definition.get("completion_outputs", definition["outputs"][:1])
            completion = [result_dir / name for name in completion_names]
            inputs = [result_dir / name for name in definition["inputs"]]
            failed_artifact = (result_dir / f"{prefix}-failed.odb").is_file()
            complete = bool(completion) and all(path.is_file() for path in completion)
            lines = log.read_text(errors="replace").splitlines() if log.is_file() else []
            lower_lines = [line.lower() for line in lines]
            skip_reason = next((line.strip() for line in lines
                                if any(pattern.lower() in line.lower()
                                       for pattern in definition.get("skip_patterns", ()))), None)
            if failed_artifact or (log.is_file() and not complete and not skip_reason):
                status = "failed"
            elif skip_reason and complete:
                status = "skipped"
            else:
                status = "completed" if complete else "not_requested"
            command = next((line.strip() for line, lower in zip(lines, lower_lines)
                            if any(token.lower() in lower for token in definition["commands"])), None)
            warnings = sum("WARN" in line.upper() for line in lines)
            errors = sum("ERROR" in line.upper() for line in lines)
            metrics, sources = _substage_metrics(metric_json, workdir, stage)
            runtime = _runtime_from_log(log)
            if log.is_file():
                finished_dt = datetime.fromtimestamp(log.stat().st_mtime)
                started_dt = finished_dt - timedelta(seconds=runtime) if runtime is not None else None
            else:
                mtimes = [path.stat().st_mtime for path in outputs if path.is_file()]
                finished_dt = datetime.fromtimestamp(max(mtimes)) if mtimes else None
                input_times = [path.stat().st_mtime for path in inputs if path.is_file()]
                started_dt = datetime.fromtimestamp(max(input_times)) if input_times else None
            reports = []
            for pattern in definition.get("report_globs", ()):
                reports.extend(path for path in report_dir.glob(pattern) if path.is_file())
            script_path = orfs_root / definition["script"]
            command_source = orfs_root / definition.get("command_source", definition["script"])
            command_evidence = [_source_ref(command_source, re.escape(token), orfs_root)
                                for token in definition["commands"]]
            gate = ("pass" if status in {"completed", "skipped"} and errors == 0 else
                    ("fail" if status == "failed" or errors else "unknown"))
            rows.append(record_dict(SubstageRecord(
                substage_id=prefix, display_name=definition["name"], stage=stage, status=status,
                script=_relative(script_path, orfs_root),
                make_target=definition.get("make_target", f"do-{prefix}"), command=command,
                started_at=started_dt.isoformat(timespec="seconds") if started_dt else None,
                finished_at=finished_dt.isoformat(timespec="seconds") if finished_dt else None,
                runtime_seconds=runtime,
                inputs=[_relative(path, workdir) for path in inputs],
                outputs=[_relative(path, workdir) for path in outputs],
                logs=[_relative(log, workdir)] if log.is_file() else [],
                reports=sorted({_relative(path, workdir) for path in reports}),
                metrics=metrics, metric_sources=sources,
                warning_count=warnings, error_count=errors, gate_status=gate,
                declared_commands=list(definition["commands"]),
                command_evidence=command_evidence, skip_reason=skip_reason,
            )))
        stages[stage] = rows
    return stages


def _artifact_type(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".odb"): return "odb_checkpoint"
    if name.endswith(".def"): return "def_layout"
    if name.endswith(".gds"): return "gds_layout"
    if name.endswith(".spef"): return "spef_parasitics"
    if name.endswith(".sdf"): return "sdf_timing"
    if name.endswith(".sdc"): return "sdc_constraints"
    if name.endswith(".v"): return "verilog_netlist"
    if name.endswith(".json"): return "metrics_json"
    if name.endswith(".rpt"): return "report"
    if name.endswith(".log"): return "runtime_log"
    if name.endswith(".mk"): return "generated_config"
    if name.endswith(".tcl"): return "tcl_script"
    return "other"


def _stage_for(path: Path) -> tuple[str | None, str | None]:
    stem = path.stem.removesuffix("-failed")
    for stage, definitions in STAGE_SUBSTAGES.items():
        for definition in definitions:
            if stem == definition["id"]:
                return stage, definition["id"]
    match = re.search(r'(?:^|/)([1-6])(?:_([\w]+))?[^/]*$', str(path))
    if not match:
        return None, None
    stage = {"1": "synth", "2": "floorplan", "3": "place", "4": "cts", "5": "route", "6": "finish"}[match.group(1)]
    return stage, None


def _artifact_registry(workdir: Path, experiment_id: str, platform: str, design: str,
                       substages: list[dict]) -> list[dict]:
    source_by_substage = {row["substage_id"]: row["script"] for row in substages}
    owner_by_path = {}
    for row in substages:
        for relative in (*row["outputs"], *row["logs"], *row["reports"]):
            owner_by_path[relative] = row
        for source in row.get("metric_sources", {}).values():
            if source.get("file"):
                owner_by_path[source["file"]] = row
    artifacts = []
    config_path = workdir / "designs" / platform / design / "config.mk"
    run_start_mtime = config_path.stat().st_mtime if config_path.is_file() else None
    roots = ("analysis", "designs", "logs", "reports", "results")
    for root_name in roots:
        root = workdir / root_name
        if not root.is_dir():
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = _relative(path, workdir)
            stage, substage = _stage_for(path)
            owner = owner_by_path.get(relative)
            if owner:
                stage, substage = owner["stage"], owner["substage_id"]
            source = source_by_substage.get(substage)
            stat = path.stat()
            current_run = run_start_mtime is None or stat.st_mtime >= run_start_mtime - 5
            artifacts.append(record_dict(ArtifactRecord(
                artifact_id=hashlib.sha256(relative.encode()).hexdigest()[:16],
                experiment_id=experiment_id, design=design, platform=platform,
                stage=stage, substage=substage, artifact_type=_artifact_type(path), path=relative,
                size=stat.st_size, generated_at=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                sha256=sha256(path), source=source, exists=True,
                previewable=path.suffix.lower() in {".gds", ".def", ".v", ".log", ".rpt", ".json"},
                current_run=current_run,
            )))
    return artifacts


EVENT_RULES = (
    (re.compile(r'(no such file|cannot open|can.t open|failed to open)', re.I), "missing_file"),
    (re.compile(r'(configuration error|invalid (?:option|value)|unknown (?:option|command))', re.I), "build_config_error"),
    (re.compile(r'(global routing failed|detailed rout(?:e|ing) failed|design is not routed)', re.I), "routing_failure"),
    (re.compile(r'(setup|hold).*(violation|negative slack)|timing violation', re.I), "timing_violation"),
    (re.compile(r'(constraint.*(?:warning|missing)|no clock|clock.*not found)', re.I), "constraint_warning"),
    (re.compile(r'(DRC).*(?:error|violation)', re.I), "drc"),
    (re.compile(r'Placement target density:\s*([\d.]+)', re.I), "placement_density"),
    (re.compile(r'Minimum Feasible Density\s+([\d.]+)', re.I), "minimum_feasible_density"),
    (re.compile(r'Total routing overflow:\s*([\d.]+)', re.I), "congestion"),
    (re.compile(r'Found\s+(\d+)\s+edge spacing violations.*?(\d+)\s+padding violations', re.I), "placement_legality"),
    (re.compile(r'Placement violations\s*(.*)', re.I), "placement_legality"),
    (re.compile(r'(out of memory|std::bad_alloc)', re.I), "out_of_memory"),
    (re.compile(r'(timed? out|timeout)', re.I), "timeout"),
    (re.compile(r'\[(ERROR|WARN(?:ING)?)\s+([A-Z]+)-\d+\]\s*(.*)', re.I), "tool_message"),
)


def _log_events(workdir: Path, platform: str, design: str) -> list[dict]:
    log_dir = workdir / "logs" / platform / design / "base"
    events = []
    for path in sorted(log_dir.glob("*.log")):
        stage, substage = _stage_for(path)
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), start=1):
            for regex, category in EVENT_RULES:
                match = regex.search(line)
                if not match:
                    continue
                upper = line.upper()
                severity = "error" if "ERROR" in upper else ("warning" if "WARN" in upper else
                           ("warning" if category in {"timing_violation", "constraint_warning", "drc", "congestion"}
                            else "info"))
                blocking = severity == "error" or category in {
                    "out_of_memory", "timeout", "missing_file", "build_config_error", "routing_failure"
                }
                parameter = "PLACE_DENSITY" if "density" in category else None
                tool = ("yosys" if path.name.startswith(("1_1_", "1_2_")) else
                        ("klayout" if "merge" in path.name else "openroad"))
                checks = {
                    "missing_file": "Check the generated config and the referenced input path.",
                    "build_config_error": "Check the exact command and option values in the generated config.",
                    "routing_failure": "Open the global/detailed route log and congestion/DRC reports.",
                    "timeout": "Check stage runtime and the configured timeout.",
                    "out_of_memory": "Check peak memory and reduce parallelism or design size.",
                }
                events.append(record_dict(LogEvent(
                    event_id=f"{path.stem}:{number}", timestamp=None, stage=stage or "unknown",
                    substage=substage, severity=severity, tool=tool, category=category,
                    message=line.strip(), source_file=_relative(path, workdir), source_line=number,
                    related_parameter=parameter, blocking=blocking,
                    suggested_check=checks.get(category, "Open the original log at this line." if blocking else None),
                )))
                break
    return events


def _evaluate(manifest: dict, metrics: dict, artifacts: list[dict], substages: list[dict],
              events: list[dict], config: dict) -> dict:
    max_runtime_seconds = float(config.get("runtime_limit_seconds", 3600))
    route_drc_max = int(config.get("route_drc_max", 0))
    route_wns_min = float(config.get("route_setup_wns_min_ns", 0))
    expected_stage = metrics.get("summary", {}).get("expected_stage", "finish")
    stage_order = ["synth", "floorplan", "place", "cts", "route", "finish"]
    required = stage_order[:stage_order.index(expected_stage) + 1] if expected_stage in stage_order else stage_order
    stages = metrics.get("stages", {})
    gates = []

    flow_status = manifest.get("status")
    gates.append(EvaluationGate("flow_exit", "ORFS process exit status",
                                "pass" if flow_status == "completed" else "fail", True,
                                f"Manifest status: {flow_status}.", []))
    missing = [stage for stage in required if stages.get(stage, {}).get("status") != "completed"]
    gates.append(EvaluationGate("required_stages", "Required stages completed", "pass" if not missing else "fail", True,
                                "All required stages completed." if not missing else f"Missing or failed: {', '.join(missing)}",
                                [{"stages": required}]))
    registered = {item["path"] for item in artifacts if item["current_run"] and item["exists"]}
    if "place" in required:
        place_odb = any(path.endswith("results/%s/%s/base/3_place.odb" %
                                     (manifest["platform"], manifest["design"]))
                        for path in registered)
        gates.append(EvaluationGate("place_checkpoint", "Place checkpoint registered",
                                    "pass" if place_odb else "fail", True,
                                    "3_place.odb belongs to this experiment." if place_odb else "3_place.odb is missing.", []))
        gp = next((row for row in substages if row["substage_id"] == "3_3_place_gp"), None)
        command_ok = bool(gp and gp.get("command") and "global_placement" in gp["command"])
        command_required = bool(config.get("require_runtime_place_command", True))
        gates.append(EvaluationGate("place_command", "Global placement command observed",
                                    "pass" if command_ok else "unknown", command_required,
                                    "Runtime log contains global_placement." if command_ok else "Command was not found in runtime logs.", []))
        dp = next((row for row in substages if row["substage_id"] == "3_5_place_dp"), None)
        legality_events = [event for event in events if event["category"] == "placement_legality"]
        legality_fail = any(event["severity"] == "error" or
                            re.search(r'Found\s+[1-9]\d*\s+edge', event["message"])
                            for event in legality_events)
        legality_ok = bool(dp and dp["status"] == "completed" and legality_events and not legality_fail)
        legality_required = bool(config.get("require_placement_legality_evidence", True))
        gates.append(EvaluationGate("placement_legality", "Placement legality evidence",
                                    "fail" if legality_fail else ("pass" if legality_ok else "unknown"), legality_required,
                                    "Placement completed with no parsed legality violation." if legality_ok else
                                    ("Placement legality violation found." if legality_fail else "Legality evidence is missing."),
                                    [{"events": [event["event_id"] for event in legality_events]}]))
    metric_count = sum(len(data.get("metrics", {})) for data in stages.values())
    gates.append(EvaluationGate("metrics_parsed", "Metrics parsed", "pass" if metric_count else "unknown", True,
                                f"Parsed {metric_count} normalized metrics." if metric_count else "No normalized metric was parsed.", []))
    if "route" in required:
        route = stages.get("route", {}).get("metrics", {})
        drc = route.get("drc_errors")
        gates.append(EvaluationGate("route_drc", "Route DRC threshold", "unknown" if drc is None else ("pass" if drc <= route_drc_max else "fail"),
                                    True, "DRC metric is unavailable." if drc is None else
                                    f"Route DRC errors: {drc}; allowed maximum: {route_drc_max}.", []))
        wns = route.get("setup_wns_ns")
        gates.append(EvaluationGate("route_timing", "Route setup timing", "unknown" if wns is None else ("pass" if wns >= route_wns_min else "fail"),
                                    True, "Route WNS is unavailable." if wns is None else
                                    f"Route setup WNS: {wns} ns; minimum: {route_wns_min} ns.", []))
    runtime = manifest.get("runtime_seconds")
    runtime_status = "unknown" if runtime is None else ("pass" if runtime <= max_runtime_seconds else "fail")
    gates.append(EvaluationGate("runtime_limit", "Runtime limit", runtime_status, True,
                                "Runtime is unavailable." if runtime is None else f"Runtime {runtime:.2f}s; limit {max_runtime_seconds}s.", []))
    stale = [item for item in artifacts if not item["current_run"]]
    gates.append(EvaluationGate("artifact_consistency", "Artifact ownership", "pass" if not stale else "fail", True,
                                "All registered artifacts are inside the current workdir." if not stale else "Stale artifacts were detected.", []))
    gate_dicts = [record_dict(gate) for gate in gates]
    failures = [gate for gate in gate_dicts if gate["blocking"] and gate["status"] == "fail"]
    unknown = [gate for gate in gate_dicts if gate["blocking"] and gate["status"] == "unknown"]
    if manifest.get("status") == "failed":
        classification = "flow_failed"
    elif failures:
        classification = "invalid"
    elif unknown:
        classification = "insufficient_evidence"
    else:
        classification = "valid"
    return {"schema_version": SCHEMA_VERSION, "classification": classification,
            "valid": classification == "valid", "gates": gate_dicts,
            "config": config,
            "blocking_failures": [gate["gate_id"] for gate in failures],
            "unknown_blocking_gates": [gate["gate_id"] for gate in unknown]}


def build_graybox_evidence(workdir: str | Path, *, project_root: str | Path,
                           orfs_root: str | Path, manifest: dict, metrics: dict,
                           requested_parameters: dict, effective_parameters: dict) -> dict:
    workdir = Path(workdir).resolve()
    project_root = Path(project_root).resolve()
    orfs_root = Path(orfs_root).expanduser().resolve()
    experiment_id = manifest.get("experiment_id") or manifest.get("run_id") or workdir.name
    platform, design = manifest["platform"], manifest["design"]
    requested = {**requested_parameters, "strategy": manifest.get("strategy")}
    parameters = _parameter_records(workdir, project_root, orfs_root, platform, design,
                                    requested, effective_parameters)
    stage_substages = _flow_substages(workdir, orfs_root, platform, design)
    substages = [row for stage in STAGE_SUBSTAGES for row in stage_substages[stage]]
    events = _log_events(workdir, platform, design)
    artifacts = _artifact_registry(workdir, experiment_id, platform, design, substages)
    config_path = project_root / "graybox_evaluation.json"
    try:
        evaluation_config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        evaluation_config = {}
    evaluation = _evaluate(manifest, metrics, artifacts, substages, events, evaluation_config)
    current_density = effective_parameters.get("openroad_place_density",
                                               effective_parameters.get("place_density"))
    suggested_density = round(min(0.95, current_density + 0.05), 2) \
        if isinstance(current_density, (int, float)) else None
    route_metrics = (metrics.get("stages", {}).get("route", {}).get("metrics") or {})
    next_experiment = {
        "schema_version": 1,
        "approval_required": True,
        "status": "proposed" if suggested_density is not None else "insufficient_evidence",
        "hypothesis": "A small PLACE_DENSITY increase may reduce area without causing route DRC or timing regression.",
        "changes": {"place_density": suggested_density} if suggested_density is not None else {},
        "controls": {key: value for key, value in requested_parameters.items() if key != "place_density"},
        "observe": ["place.estimated_wirelength_um", "route.wirelength_um", "route.setup_wns_ns", "route.drc_errors"],
        "success_criteria": [
            "All required stages and hard gates pass.",
            f"route.drc_errors <= {route_metrics.get('drc_errors', 0)}",
            "route.setup_wns_ns does not regress beyond the configured timing gate.",
        ],
        "failure_criteria": ["Any blocking hard gate fails.", "Route DRC increases.", "Route timing crosses the configured limit."],
        "rollback": {"place_density": current_density},
    }
    environment = {
        "hostname": host_platform.node() or None,
        "machine": host_platform.machine() or None,
        "os": host_platform.platform() or None,
        "python": host_platform.python_version(),
        "pid": os.getpid(),
    }
    graybox = {
        "schema_version": SCHEMA_VERSION, "experiment_id": experiment_id,
        "design": design, "platform": platform, "environment": environment,
        "parameters": parameters,
        "stages": {stage: {"substages": rows}
                   for stage, rows in stage_substages.items()},
        "metric_timeline": {
            "stages": [{"stage": stage, "status": data.get("status"),
                        "metrics": data.get("metrics", {}), "sources": data.get("sources", {})}
                       for stage, data in metrics.get("stages", {}).items()],
            "place_substages": [{"substage": row["substage_id"], "status": row["status"],
                                 "metrics": row["metrics"], "sources": row["metric_sources"]}
                                for row in stage_substages["place"]],
            "substages": [{"stage": stage, "substage": row["substage_id"],
                            "status": row["status"], "metrics": row["metrics"],
                            "sources": row["metric_sources"]}
                           for stage, rows in stage_substages.items() for row in rows],
        },
        "artifact_summary": {"count": len(artifacts),
                             "by_type": {kind: sum(item["artifact_type"] == kind for item in artifacts)
                                         for kind in sorted({item["artifact_type"] for item in artifacts})}},
        "event_summary": {"count": len(events),
                          "errors": sum(event["severity"] == "error" for event in events),
                          "warnings": sum(event["severity"] == "warning" for event in events)},
        "evaluation": evaluation,
        "next_experiment": next_experiment,
    }
    return {"graybox": graybox, "parameters": parameters, "substages": substages,
            "stage_substages": stage_substages,
            "artifacts": artifacts, "events": events, "evaluation": evaluation,
            "environment": environment}
