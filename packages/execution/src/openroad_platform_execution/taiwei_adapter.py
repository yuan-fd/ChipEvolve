"""Workspace-isolated protocol adapter for the official TaiWei gcd ORD flow."""

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
        if task["inputs"] != {"flow": "ord", "tech": "asap7_3D", "case": "gcd"}:
            return _fail(args.result, started, "policy_rejected", "Only ord/asap7_3D/gcd is allowed")
        source = Path(os.environ["TAIWEI_SOURCE"]).resolve()
        staged = args.result.parent / "taiwei-source"
        _archive(source, staged)
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
        }
        snapshot_path = args.result.parent / "toolchain_snapshot.json"
        _write(snapshot_path, snapshot)
        env = os.environ.copy()
        env.update({"ORFS_DIR": os.environ["TAIWEI_ORFS_ROOT"],
                    "FLOW_HOME": str(staged), "WORK_DIR": str(staged),
                    "MODULEPATH": os.pathsep.join(filter(None, (
                        str(module_root), env.get("MODULEPATH", "")))),
                    "NUM_CORES": env.get("TAIWEI_NUM_CORES", "8")})
        command = [sys.executable, "run_experiments.py", "--flow", "ord",
                   "--tech", "asap7_3D", "--case", "gcd", "--run-only",
                   "--status-interval", "5"]
        with log.open("w", encoding="utf-8") as stream:
            completed = subprocess.run(command, cwd=staged, env=env,
                                       stdout=stream, stderr=subprocess.STDOUT, text=True)
        if log.stat().st_size == 0:
            log.write_text("TaiWei command completed without console output.\n", encoding="utf-8")
        if completed.returncode:
            return _fail(args.result, started, "upstream_failure",
                         "TaiWei gcd flow returned non-zero", completed.returncode)
        _postprocess(staged)
        artifacts = _discover(args.result.parent, staged)
        required = {"three_d_eval", "three_d_summary", "gds", "def", "odb", "netlist"}
        missing = required - {item["kind"] for item in artifacts}
        if missing:
            return _fail(args.result, started, "artifact_missing",
                         f"TaiWei outputs missing: {sorted(missing)}")
        artifacts.extend([{"kind": "toolchain_snapshot", "path": snapshot_path.name},
                          {"kind": "log", "path": log.name}])
        metrics = _metrics(staged)
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


def _discover(workspace: Path, staged: Path) -> list[dict]:
    # Keep discovery inside run-output roots.  A broad **/*.gds glob would
    # incorrectly accept Nangate's platform library GDS as the gcd result.
    specs = (("three_d_eval", "reports/asap7_3D/gcd/*/openroad_eval.json"),
             ("three_d_eval", "reports/openroad_eval.json"),
             ("three_d_summary", "logs/asap7_3D/gcd/*/final_summary.txt"),
             ("three_d_summary", "logs/final_summary.txt"),
             ("gds", "results/asap7_3D/gcd/*/6_final.gds"),
             ("gds", "results/final.gds"),
             ("def", "results/asap7_3D/gcd/*/6_final.def"),
             ("def", "results/6_final.def"),
             ("odb", "results/asap7_3D/gcd/*/6_final.odb"),
             ("odb", "results/6_final.odb"),
             ("netlist", "results/asap7_3D/gcd/*/6_final.v"),
             ("netlist", "results/6_final.v"),
             ("sdc", "results/asap7_3D/gcd/*/6_final.sdc"),
             ("spef", "results/asap7_3D/gcd/*/6_final.spef"),
             ("three_d_report", "logs/asap7_3D/gcd/*/cross_tier_nets*.rpt"),
             ("three_d_report", "logs/asap7_3D/gcd/*/cross_tier_nets*.list"),
             ("three_d_report", "results/asap7_3D/gcd/*/streamout_provenance.json"),
             ("three_d_report", "results/asap7_3D/gcd/*/gds_view_provenance.json"),
             ("three_d_report", "results/asap7_3D/gcd/*/tier_view_metrics.json"),
             ("layout_view", "results/asap7_3D/gcd/*/*2d*.png"),
             ("three_d_view", "results/asap7_3D/gcd/*/*3d*.png"),
             ("three_d_view", "results/asap7_3D/gcd/*/*3d*.svg"),
             ("three_d_view", "reports/*3d*.png"))
    artifacts = []
    for kind, pattern in specs:
        for path in sorted(staged.glob(pattern)):
            if path.is_file() and path.stat().st_size:
                artifacts.append({"kind": kind, "path": str(path.relative_to(workspace))})
    return artifacts


def _postprocess(staged: Path) -> None:
    final_defs = sorted(staged.glob("results/asap7_3D/gcd/*/6_final.def"))
    if not final_defs:
        # Unit/third-party fixtures may provide a shallow prebuilt stream.
        fixture_streams = tuple(staged.glob("results/final.gds"))
        if any(path.is_file() and path.stat().st_size for path in fixture_streams):
            return
        raise FileNotFoundError("TaiWei final DEF is missing")
    final_def = final_defs[-1]
    result_dir = final_def.parent
    gds = result_dir / "6_final.gds"
    provenance = stream_out_gds(staged, final_def, gds)
    write_json(result_dir / "streamout_provenance.json", provenance)
    view_provenance = render_gds_views(gds, result_dir)
    write_json(result_dir / "gds_view_provenance.json", view_provenance)
    view = result_dir / "gcd_final_3d_tiers.svg"
    view_metrics = render_tier_view(final_def, view)
    write_json(result_dir / "tier_view_metrics.json", view_metrics)


def _metrics(staged: Path) -> list[dict]:
    paths = sorted(staged.glob("reports/asap7_3D/gcd/*/openroad_eval.json"))
    if not paths:
        return []
    payload = json.loads(paths[-1].read_text(encoding="utf-8"))
    metrics = []
    for name, record in sorted(payload.items()):
        if name.startswith("_"):
            continue
        value = record.get("value") if isinstance(record, dict) else record
        metrics.append({"name": name, "value": value, "parser_id": "taiwei-openroad-eval",
                        "parser_version": "1.0.0", "context": {"design": "gcd", "dimension": "3D"}})
    summary_paths = sorted(staged.glob("logs/asap7_3D/gcd/*/final_summary.txt"))
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
                                "context": {"design": "gcd", "dimension": "3D"}})
    tier_paths = sorted(staged.glob("results/asap7_3D/gcd/*/tier_view_metrics.json"))
    if tier_paths:
        tiers = json.loads(tier_paths[-1].read_text(encoding="utf-8"))
        for key in ("upper_instances", "bottom_instances", "hbt_named_instances"):
            if key in tiers:
                metrics.append({"name": f"finish__placement__{key}", "value": tiers[key],
                                "parser_id": "taiwei-tier-view", "parser_version": "1.0.0",
                                "context": {"design": "gcd", "dimension": "3D"}})
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
