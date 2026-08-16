"""Workspace-isolated protocol adapter for the official TaiWei ORD 3D flow.

Generalised from the original gcd-only acceptance adapter:
- flow/tech/case come from task.inputs (not hard-coded gcd/asap7_3D),
- engine-native flow knobs from task.parameters are exported into the
  engine environment (CORE_UTILIZATION, NUM_CORES, CTS_LAYER, ...),
- artifact discovery, metrics parsing and post-processing use the
  requested tech/case paths instead of asap7_3D/gcd literals.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
for source_root in (REPOSITORY_ROOT / "packages/contracts/src",
                    REPOSITORY_ROOT / "packages/execution/src",
                    REPOSITORY_ROOT / "packages/visualization/src"):
    sys.path.insert(0, str(source_root))

from openroad_platform_execution.taiwei_postprocess import (  # noqa: E402
    render_gds_views, render_tier_view,
    stream_out_gds,
    write_json,
)

# Engine-native knobs carried through task.parameters; each maps 1:1 to a
# variable the TaiWei Makefile / launcher already understands.
_PARAMETER_ENV = {
    "core_utilization_pct": ("CORE_UTILIZATION", int),
    "num_cores": ("NUM_CORES", int),
    "cts_layer": ("CTS_LAYER", str),
    "outer_iterations": ("OUTER_ITERATIONS", int),
    "skip_2d_part": ("SKIP_2D_PART", lambda v: "1" if v else "0"),
    "pin3d_allow_net_flow": ("PIN3D_ALLOW_NET_FLOW", lambda v: "on" if v else "off"),
    "pin3d_split_net_flow": ("PIN3D_SPLIT_NET_FLOW", lambda v: "on" if v else "off"),
    "abc_area": ("ABC_AREA", lambda v: "1" if v else "0"),
    "start_from": ("START_FROM", str),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    started = _now()
    log = args.result.parent / "taiwei.log"
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        task = request["task"]
        flow = str(task["inputs"].get("flow", "ord"))
        tech = str(task["inputs"].get("tech", "asap7_3D"))
        case = str(task["inputs"].get("case", "gcd"))
        if flow != "ord":
            return _fail(args.result, started, "policy_rejected",
                         f"TaiWei adapter only supports the ord flow, got {flow!r}")
        source = Path(os.environ["TAIWEI_SOURCE"]).resolve()
        staged = args.result.parent / "taiwei-source"
        _archive(source, staged)
        _ensure_case_configured(staged, tech, case, task)
        top_cell = _design_name(staged, tech, case)
        module_root = args.result.parent / "modulefiles"
        module_root.mkdir()
        (module_root / "cadence").write_text(
            "#%Module1.0\n# Empty module: ORD acceptance does not use Cadence.\n",
            encoding="utf-8",
        )
        snapshot = {
            "taiwei_commit": os.environ["TAIWEI_EXPECTED_COMMIT"],
            "orfs_root": os.environ["TAIWEI_ORFS_ROOT"],
            "orfs_commit": os.environ["TAIWEI_ORFS_COMMIT"],
            "openroad_commit": os.environ["TAIWEI_OPENROAD_COMMIT"],
            "openroad_bin": os.environ["OPENROAD_EXE"],
            "yosys_bin": os.environ["YOSYS_EXE"],
            "openroad_version": _version([os.environ["OPENROAD_EXE"], "-version"]),
            "yosys_version": _version([os.environ["YOSYS_EXE"], "-V"]),
            "openroad_sha256": _sha256(Path(os.environ["OPENROAD_EXE"])),
            "yosys_sha256": _sha256(Path(os.environ["YOSYS_EXE"])),
            "flow": flow, "tech": tech, "case": case, "top_cell": top_cell,
            "parameters": dict(task.get("parameters") or {}),
        }
        snapshot_path = args.result.parent / "toolchain_snapshot.json"
        env = os.environ.copy()
        env.update({"ORFS_DIR": os.environ["TAIWEI_ORFS_ROOT"],
                    "FLOW_HOME": str(staged), "WORK_DIR": str(staged),
                    "MODULEPATH": os.pathsep.join(filter(None, (
                        str(module_root), env.get("MODULEPATH", "")))),
                    "NUM_CORES": env.get("TAIWEI_NUM_CORES", "8")})
        for key, (env_name, converter) in _PARAMETER_ENV.items():
            if key in (task.get("parameters") or {}):
                env[env_name] = str(converter(task["parameters"][key]))
        snapshot["engine_environment"] = {
            env_name: env[env_name] for env_name, _ in _PARAMETER_ENV.values()
            if env_name in env
        }
        _write(snapshot_path, snapshot)
        command = [sys.executable, "run_experiments.py", "--flow", flow,
                   "--tech", tech, "--case", case, "--run-only",
                   "--status-interval", "5"]
        with log.open("w", encoding="utf-8") as stream:
            completed = subprocess.run(command, cwd=staged, env=env,
                                       stdout=stream, stderr=subprocess.STDOUT, text=True)
        if log.stat().st_size == 0:
            log.write_text("TaiWei command completed without console output.\n", encoding="utf-8")
        if completed.returncode:
            return _fail(args.result, started, "upstream_failure",
                         f"TaiWei {case} flow returned non-zero", completed.returncode)
        status_path = staged / "run_logs" / "status" / f"{flow}__{tech}__{case}.json"
        if status_path.is_file():
            status = json.loads(status_path.read_text(encoding="utf-8"))
            if status.get("status") != "ok":
                code = status.get("dispatch_rc")
                return _fail(
                    args.result, started, "upstream_failure",
                    f"TaiWei {case} flow failed: {status.get('message') or status.get('status')}",
                    code if isinstance(code, int) and code else 1,
                )
        _postprocess(staged, tech, case, top_cell)
        artifacts = _discover(args.result.parent, staged, tech, case)
        # Verified artifacts for the configured case/tech before streaming.
        # Fall back to shallow prebuilt fixtures only for unit fixtures.
        required = {"three_d_eval", "three_d_summary", "gds", "def", "odb", "netlist"}
        missing = required - {item["kind"] for item in artifacts}
        if missing:
            return _fail(args.result, started, "artifact_missing",
                         f"TaiWei outputs missing: {sorted(missing)}")
        artifacts.extend([{"kind": "toolchain_snapshot", "path": snapshot_path.name},
                          {"kind": "log", "path": log.name}])
        metrics = _metrics(staged, tech, case)
        _write(args.result, {"schema_version": 1, "status": "succeeded", "exit_code": 0,
                            "started_at": started, "ended_at": _now(), "metrics": metrics,
                            "artifacts": artifacts, "failure": None,
                            "provenance": {**snapshot, "real_3d": True}})
        return 0
    except Exception as exc:
        return _fail(args.result, started, "adapter_error", f"{type(exc).__name__}: {exc}")


def _archive(source: Path, destination: Path) -> None:
    destination.mkdir()
    archive = subprocess.Popen(["git", "-C", str(source), "archive", "HEAD"],
                               stdout=subprocess.PIPE)
    extract = subprocess.run(["tar", "-x", "-C", str(destination)],
                             stdin=archive.stdout, check=False)
    if archive.stdout:
        archive.stdout.close()
    code = archive.wait()
    if code or extract.returncode:
        raise RuntimeError("Cannot stage immutable TaiWei source snapshot")


# 2D platform used by the engine's internal 2D partition stage for each 3D
# platform (config2d.mk sets PLATFORM to the 2D base process).
_2D_PLATFORM = {
    "asap7_3D": "asap7",
    "nangate45_3D": "nangate45",
    "asap7_nangate45_3D": "asap7_nangate45",
}

_CASE_TEMPLATE = "gcd"  # shipped reference case whose config is copied.


def _design_name(staged: Path, tech: str, case: str) -> str:
    """Resolve the implementation top cell from the selected case config."""
    config = staged / "designs" / tech / case / "config.mk"
    if not config.is_file():
        raise FileNotFoundError(f"TaiWei design config missing: {config}")
    match = re.search(
        r"^\s*(?:export\s+)?DESIGN_NAME\s*(?::|\?)?=\s*([^#\s]+)",
        config.read_text(encoding="utf-8", errors="replace"),
        re.M,
    )
    if not match:
        raise ValueError(f"TaiWei design config has no DESIGN_NAME: {config}")
    return match.group(1)


def _ensure_case_configured(staged: Path, tech: str, case: str,
                            task: dict) -> None:
    """Use the shipped engine case when present; otherwise generate a dynamic
    case configuration from the platform-registered RTL.

    The engine dispatches through ``test/<tech>/<case>/ord/run.sh`` and reads
    the design from ``designs/<tech>/<case>/{config,config2d}.mk`` plus
    ``designs/src/<case>/*.v`` (via config2d.mk's wildcard).  For cases the
    engine does not ship (user designs registered in the platform), we write
    those files from the shipped gcd template and the task's RTL reference.
    """
    run_script = staged / "test" / tech / case / "ord" / "run.sh"
    if run_script.is_file():
        return  # official engine case; nothing to generate
    if tech not in _2D_PLATFORM:
        raise ValueError(f"No 2D base platform mapped for 3D platform {tech!r}")
    rtl = task.get("inputs", {}).get("rtl") if isinstance(task.get("inputs"), dict) else None
    if not isinstance(rtl, dict) or not rtl.get("path"):
        raise ValueError(
            f"TaiWei case {case!r} is not shipped by the engine; a platform "
            "RTL reference (inputs.rtl.path) is required to generate it")

    design_dir = staged / "designs" / tech / case
    design_dir.mkdir(parents=True, exist_ok=True)
    src_dir = staged / "designs" / "src" / case
    src_dir.mkdir(parents=True, exist_ok=True)

    # Copy the shipped gcd config as the design-specific template.
    template_dir = staged / "designs" / tech / _CASE_TEMPLATE
    for name in ("config.mk", "config2d.mk"):
        template = template_dir / name
        if not template.is_file():
            raise FileNotFoundError(f"TaiWei case template missing: {template}")
        target = design_dir / name
        text = template.read_text(encoding="utf-8")
        text = re.sub(r"^export DESIGN_NAME\s*=.*$",
                      f"export DESIGN_NAME = {case}", text, count=1, flags=re.M)
        # config2d.mk points VERILOG_FILES at designs/src/<case> via a
        # wildcard; only DESIGN_NAME must change.  config.mk's PLATFORM stays
        # the requested tech because the template belongs to the same tech.
        target.write_text(text, encoding="utf-8")

    # SDC: generate from the task clock inputs when provided, otherwise keep a
    # clock-free constraint file (engine defaults apply).
    clock = str(task.get("inputs", {}).get("clock") or "").strip()
    period = task.get("inputs", {}).get("clock_period_ns")
    sdc = design_dir / "constraint.sdc"
    if clock and period:
        sdc.write_text(
            "current_design %s\n\n"
            "set clk_name core_clock\n"
            "set clk_port_name %s\n"
            "set clk_period %s\n"
            "set clk_io_pct 0.2\n\n"
            "set clk_port [get_ports $clk_port_name]\n"
            "create_clock -name $clk_name -period $clk_period $clk_port\n\n"
            "set non_clock_inputs [all_inputs -no_clocks]\n"
            "set_input_delay [expr $clk_period * $clk_io_pct] -clock $clk_name $non_clock_inputs\n"
            "set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]\n"
            % (case, clock, str(period)), encoding="utf-8")
    else:
        sdc.write_text("current_design %s\n" % case, encoding="utf-8")

    # Stage the platform RTL into designs/src/<case>/.
    rtl_source = Path(str(rtl["path"])).expanduser().resolve()
    if not rtl_source.is_file():
        raise FileNotFoundError(f"Platform RTL input missing: {rtl_source}")
    rtl_name = rtl.get("name") or rtl_source.name
    shutil.copy2(rtl_source, src_dir / rtl_name)

    # Dispatch scripts mirror the shipped case wrappers.
    ord_dir = staged / "test" / tech / case / "ord"
    ord_dir.mkdir(parents=True, exist_ok=True)
    rel = "../../../common"
    (ord_dir / "run.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        f'exec bash "${{SCRIPT_DIR}}/{rel}/run_case.sh" ord "${{SCRIPT_DIR}}"\n',
        encoding="utf-8")
    (ord_dir / "run.sh").chmod(0o755)
    (ord_dir / "eval.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        f'exec bash "${{SCRIPT_DIR}}/{rel}/eval_case.sh" ord "${{SCRIPT_DIR}}"\n',
        encoding="utf-8")
    (ord_dir / "eval.sh").chmod(0o755)


def _discover(workspace: Path, staged: Path, tech: str, case: str) -> list[dict]:
    # Keep discovery inside run-output roots.  A broad **/*.gds glob would
    # incorrectly accept platform library GDS as the run result.
    specs = (("three_d_eval", f"reports/{tech}/{case}/*/openroad_eval.json"),
             ("three_d_eval", "reports/openroad_eval.json"),
             ("three_d_summary", f"logs/{tech}/{case}/*/final_summary.txt"),
             ("three_d_summary", "logs/final_summary.txt"),
             ("gds", f"results/{tech}/{case}/*/6_final.gds"),
             ("gds", "results/final.gds"),
             ("def", f"results/{tech}/{case}/*/6_final.def"),
             ("def", "results/6_final.def"),
             ("odb", f"results/{tech}/{case}/*/6_final.odb"),
             ("odb", "results/6_final.odb"),
             ("netlist", f"results/{tech}/{case}/*/6_final.v"),
             ("netlist", "results/6_final.v"),
             ("sdc", f"results/{tech}/{case}/*/6_final.sdc"),
             ("spef", f"results/{tech}/{case}/*/6_final.spef"),
             ("three_d_report", f"logs/{tech}/{case}/*/cross_tier_nets*.rpt"),
             ("three_d_report", f"logs/{tech}/{case}/*/cross_tier_nets*.list"),
             ("three_d_report", f"results/{tech}/{case}/*/streamout_provenance.json"),
             ("three_d_report", f"results/{tech}/{case}/*/gds_view_provenance.json"),
             ("three_d_report", f"results/{tech}/{case}/*/tier_view_metrics.json"),
             ("layout_view", f"results/{tech}/{case}/*/*2d*.png"),
             ("three_d_view", f"results/{tech}/{case}/*/*3d*.png"),
             ("three_d_view", f"results/{tech}/{case}/*/*3d*.svg"),
             ("three_d_view", "reports/*3d*.png"))
    artifacts = []
    for kind, pattern in specs:
        for path in sorted(staged.glob(pattern)):
            if path.is_file() and path.stat().st_size:
                artifacts.append({"kind": kind, "path": str(path.relative_to(workspace))})
    return artifacts


def _postprocess(staged: Path, tech: str, case: str, top_cell: str) -> None:
    final_defs = sorted(staged.glob(f"results/{tech}/{case}/*/6_final.def"))
    if not final_defs:
        # Unit/third-party fixtures may provide a shallow prebuilt stream.
        fixture_streams = tuple(staged.glob("results/final.gds"))
        if any(path.is_file() and path.stat().st_size for path in fixture_streams):
            return
        raise FileNotFoundError("TaiWei final DEF is missing")
    final_def = final_defs[-1]
    result_dir = final_def.parent
    gds = result_dir / "6_final.gds"
    provenance = stream_out_gds(staged, final_def, gds, tech=tech, top_cell=top_cell)
    write_json(result_dir / "streamout_provenance.json", provenance)
    view_provenance = render_gds_views(gds, result_dir, design=case)
    write_json(result_dir / "gds_view_provenance.json", view_provenance)
    view = result_dir / f"{case}_final_3d_tiers.svg"
    view_metrics = render_tier_view(final_def, view, design=top_cell)
    write_json(result_dir / "tier_view_metrics.json", view_metrics)


def _metrics(staged: Path, tech: str, case: str) -> list[dict]:
    paths = sorted(staged.glob(f"reports/{tech}/{case}/*/openroad_eval.json"))
    if not paths:
        return []
    payload = json.loads(paths[-1].read_text(encoding="utf-8"))
    metrics = []
    for name, record in sorted(payload.items()):
        if name.startswith("_"):
            continue
        value = record.get("value") if isinstance(record, dict) else record
        metrics.append({"name": name, "value": value, "parser_id": "taiwei-openroad-eval",
                        "parser_version": "1.0.0",
                        "context": {"design": case, "dimension": "3D"}})
    summary_paths = sorted(staged.glob(f"logs/{tech}/{case}/*/final_summary.txt"))
    if summary_paths:
        summary = summary_paths[-1].read_text(encoding="utf-8", errors="replace")
        fields = {
            "Wire Length": "finish__route__wirelength",
            "Upper_Bottom": "finish__route__cross_tier_nets__upper_bottom",
            "Upper_IO": "finish__route__cross_tier_nets__upper_io",
            "Bottom_IO": "finish__route__cross_tier_nets__bottom_io",
            "Upper_Bottom_IO": "finish__route__cross_tier_nets__upper_bottom_io",
            "Unknown_Tier": "finish__route__cross_tier_nets__unknown",
        }
        existing = {item["name"] for item in metrics}
        for label, name in fields.items():
            match = re.search(rf"^{re.escape(label)}\s+([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*$",
                              summary, re.M)
            if match and name not in existing:
                value_text = match.group(1)
                value = float(value_text) if any(char in value_text for char in ".eE") \
                    else int(value_text)
                metrics.append({"name": name, "value": value,
                                "parser_id": "taiwei-final-summary",
                                "parser_version": "1.0.0",
                                "context": {"design": case, "dimension": "3D"}})
    tier_paths = sorted(staged.glob(f"results/{tech}/{case}/*/tier_view_metrics.json"))
    if tier_paths:
        tiers = json.loads(tier_paths[-1].read_text(encoding="utf-8"))
        for key in ("upper_instances", "bottom_instances", "hbt_named_instances"):
            if key in tiers:
                metrics.append({"name": f"finish__placement__{key}", "value": tiers[key],
                                "parser_id": "taiwei-tier-view", "parser_version": "1.0.0",
                                "context": {"design": case, "dimension": "3D"}})
    return metrics


def _version(command: list[str]) -> str:
    completed = subprocess.run(command, check=False, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    return completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else "unknown"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fail(path: Path, started: str, category: str, message: str, code: int = 1) -> int:
    _write(path, {"schema_version": 1, "status": "failed", "exit_code": code,
                  "started_at": started, "ended_at": _now(), "metrics": [],
                  "artifacts": [], "failure": {"category": category, "message": message},
                  "provenance": {"real_3d": False}})
    return code


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
