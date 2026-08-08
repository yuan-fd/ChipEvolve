#!/usr/bin/env python3
"""Bounded low-cost adapter for five EDACraft extension components."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    started = _now()
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        task = request["task"]
        slug = os.environ["EDACRAFT_COMPONENT_SLUG"]
        root = Path(os.environ["EDACRAFT_ROOT"]).resolve()
        expected = os.environ["EDACRAFT_EXPECTED_COMMIT"]
        if _git(root, "rev-parse", "HEAD") != expected:
            return _fail(args.result, started, "source_mismatch", "EDACraft fixed commit changed")
        if _git(root, "status", "--porcelain=v1"):
            return _fail(args.result, started, "source_dirty", "EDACraft source tree is not clean")
        component = os.environ["EDACRAFT_COMPONENT"]
        if task.get("plugin_id") != f"edacraft-{slug}":
            return _fail(args.result, started, "identity_mismatch", "Task/plugin identity mismatch")
        workspace = args.result.parent.resolve()
        snapshot = _snapshot(root, component, expected, task["parameters"].get("mode"))
        snapshot_path = workspace / "source_snapshot.json"
        _write(snapshot_path, snapshot)
        generated, metrics, claims = _run_smoke(slug, root, workspace, task)
        numerical_solver = slug in {"cktcraft", "momcraft"}
        report_path = workspace / "capability_report.json"
        _write(report_path, {
            "schema_version": 1,
            "component": component,
            "plugin_id": task["plugin_id"],
            "smoke_mode": task["parameters"].get("mode"),
            "claims": claims,
            "safety": {
                "arbitrary_shell_exposed": False,
                "user_file_write_exposed": False,
                "full_solver_executed": numerical_solver,
                "signoff_claimed": False,
                "runtime_authoritative": True,
            },
        })
        artifacts = [
            {"kind": "report", "path": report_path.name},
            {"kind": "source_snapshot", "path": snapshot_path.name},
            *generated,
        ]
        _write(args.result, {
            "schema_version": 1,
            "status": "succeeded",
            "exit_code": 0,
            "started_at": started,
            "ended_at": _now(),
            "metrics": metrics,
            "artifacts": artifacts,
            "failure": None,
            "provenance": snapshot,
        })
        return 0
    except Exception as exc:
        return _fail(args.result, started, "adapter_error", f"{type(exc).__name__}: {exc}")


def _run_smoke(slug: str, root: Path, workspace: Path, task: dict):
    if slug == "rtlcraft":
        return _rtlcraft(root, workspace)
    if slug == "edacode":
        return _edacode(root, workspace, task)
    if slug == "tcadcraft":
        return _tcadcraft(root, workspace, task)
    if slug == "momcraft":
        return _momcraft(root, workspace, task)
    if slug == "cktcraft":
        return _cktcraft(root, workspace, task)
    raise ValueError(f"Unsupported EDACraft component: {slug}")


def _rtlcraft(root: Path, workspace: Path):
    sys.path.insert(0, str(root / "RTLCraft"))
    from rtlgen.dsl import Else, If, Input, Module, Output, Reg, VerilogEmitter

    class PlatformAccumulator(Module):
        def __init__(self):
            super().__init__("PlatformAccumulator")
            self.clk = Input(1, "clk")
            self.rst = Input(1, "rst")
            self.inp = Input(8, "inp")
            self.out = Output(8, "out")
            self.acc = Reg(8, "acc")

            @self.comb
            def _comb():
                self.out <<= self.acc

            @self.seq(self.clk, self.rst)
            def _seq():
                with If(self.rst == 1):
                    self.acc <<= 0
                with Else():
                    self.acc <<= self.acc + self.inp

    text = VerilogEmitter().emit(PlatformAccumulator())
    if "module PlatformAccumulator" not in text or "always" not in text:
        raise RuntimeError("RTLCraft did not emit the expected SystemVerilog")
    rtl = workspace / "PlatformAccumulator.sv"
    rtl.write_text(text, encoding="utf-8")
    return ([{"kind": "generated_rtl", "path": rtl.name}],
            [{"name": "rtlcraft.emitted_bytes", "value": len(text.encode()), "unit": "bytes"}],
            ["Imported fixed upstream rtlgen DSL", "Emitted a real accumulator RTL module"])


def _edacode(root: Path, workspace: Path, task: dict):
    base = root / "EDACode" / "src" / "eda_agent"
    required = [
        base / "core" / "agent.py",
        base / "providers" / "openai_provider.py",
        base / "server" / "vscode_server.py",
        base / "tools" / "bash.py",
        base / "tools" / "file_tools.py",
    ]
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"EDACode source surface is incomplete: {missing}")
    prompt = str(task.get("inputs", {}).get("prompt") or "Review an analog design request")[:1000]
    proposal = workspace / "agent_proposal.json"
    _write(proposal, {
        "schema_version": 1, "mode": "proposal-only", "request": prompt,
        "proposed_steps": [
            "Clarify supply, process, loading, and target metrics",
            "Prepare a reviewable SPICE netlist candidate",
            "Request explicit approval before any simulator handoff",
        ],
        "registered_tools": [], "shell_available": False, "file_write_available": False,
        "provider_contract": "EDACode BaseProvider-compatible; no credential used in smoke",
    })
    return ([{"kind": "agent_proposal", "path": proposal.name}],
            [{"name": "edacode.proposed_steps", "value": 3, "unit": "count"}],
            ["Used the fixed EDACode provider/agent contract as a proposal-only boundary",
             "No upstream shell, file-write, background, or EDA execution tool was registered"])


def _tcadcraft(root: Path, workspace: Path, task: dict):
    import numpy as np

    params = task.get("parameters", {})
    length_nm = _bounded_float(params.get("length_nm", 10.0), 1.0, 10_000.0, "length_nm")
    width_nm = _bounded_float(params.get("width_nm", 5.0), 1.0, 10_000.0, "width_nm")
    height_nm = _bounded_float(params.get("height_nm", 3.0), 1.0, 10_000.0, "height_nm")

    # Load the leaf module directly: importing ``tcad`` eagerly loads the full
    # solver stack (including optional SciPy), which is outside this smoke.
    module_path = root / "TCADCraft" / "tcad" / "geometry" / "shapes.py"
    spec = importlib.util.spec_from_file_location("edacraft_tcad_shapes", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load upstream TCADCraft geometry module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    box = module.Box(0.0, length_nm * 1e-9, 0.0, width_nm * 1e-9,
                     0.0, height_nm * 1e-9)
    mask = box.contains(
        np.array([length_nm * .5e-9, length_nm * 1.2e-9]),
        np.array([width_nm * .5e-9, width_nm * .5e-9]),
        np.array([height_nm * .5e-9, height_nm * .5e-9]),
    )
    geometry = workspace / "device_geometry.json"
    _write(geometry, {"primitive": "Box", "bbox_m": box.bbox(),
                      "dimensions_nm": {"length": length_nm, "width": width_nm,
                                        "height": height_nm},
                      "inside": mask.tolist()})
    view = workspace / "device_structure.svg"
    view.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="900" height="420" viewBox="0 0 900 420">'
        '<rect width="900" height="420" fill="#fff"/><text x="45" y="52" font-family="sans-serif" '
        'font-size="24" fill="#171717">TCADCraft device structure validation</text>'
        '<polygon points="180,145 650,145 750,95 280,95" fill="#f6d7b2" stroke="#202020"/>'
        '<polygon points="650,145 750,95 750,270 650,320" fill="#dcad7c" stroke="#202020"/>'
        '<rect x="180" y="145" width="470" height="175" fill="#fbebdc" stroke="#202020"/>'
        f'<text x="180" y="365" font-family="monospace" font-size="18" fill="#555">'
        f'L={length_nm:g} nm · W={width_nm:g} nm · H={height_nm:g} nm</text></svg>\n',
        encoding="utf-8",
    )
    invariant_path = root / "TCADCraft" / "tcad" / "physics" / "invariants.py"
    invariant_spec = importlib.util.spec_from_file_location("edacraft_tcad_invariants", invariant_path)
    if invariant_spec is None or invariant_spec.loader is None:
        raise RuntimeError("Cannot load upstream TCADCraft physics invariants")
    invariants = importlib.util.module_from_spec(invariant_spec)
    invariant_spec.loader.exec_module(invariants)
    carriers_n = np.array([1.0e16, 2.0e16, 3.0e16])
    carriers_p = np.array([3.0e15, 2.0e15, 1.0e15])
    potential = np.array([0.0, 0.2, 0.5])
    invariants.PhysicsInvariants.check_carriers(carriers_n, carriers_p)
    invariants.PhysicsInvariants.check_potential(potential, -1.0, 1.0)
    invariants.PhysicsInvariants.check_divergence_stencil()
    validation = workspace / "physics_validation.json"
    _write(validation, {"schema_version": 1, "checks": {
        "carrier_nonnegative": True, "potential_bounded": True,
        "divergence_stencil": True}, "full_solver_executed": False,
        "full_solver_build_status": "blocked-by-upstream-header-source-mismatch"})
    return ([{"kind": "device_geometry", "path": geometry.name},
             {"kind": "device_view", "path": view.name},
             {"kind": "physics_validation", "path": validation.name}],
            [{"name": "tcadcraft.physics_checks", "value": 3, "unit": "count"},
             {"name": "tcadcraft.device_volume_nm3",
              "value": length_nm * width_nm * height_nm, "unit": "nm3"}],
            ["Executed fixed upstream geometry and physics-invariant code",
             "Full TCAD solver is not claimed because the pinned source does not compile consistently"])


def _momcraft(root: Path, workspace: Path, task: dict):
    import numpy as np

    python_path = Path(os.environ["EDACRAFT_MOM_PYTHONPATH"]).resolve()
    if not python_path.is_dir():
        raise RuntimeError("Pinned MoMCraft runtime package is missing")
    extensions = tuple((python_path / "mom").glob("_mom*.so"))
    if len(extensions) != 1 or _sha256(extensions[0]) != os.environ["EDACRAFT_MOMCRAFT_SHA256"]:
        raise RuntimeError("Pinned MoMCraft extension SHA-256 mismatch")
    sys.path.insert(0, str(python_path))
    import mom

    params = task.get("parameters", {})
    length_mm = _bounded_float(params.get("length_mm", 2.0), .01, 100.0, "length_mm")
    width_mm = _bounded_float(params.get("width_mm", .5), .001, 20.0, "width_mm")
    height_mm = _bounded_float(params.get("height_mm", .3), .001, 20.0, "height_mm")
    eps_eff = _bounded_float(params.get("eps_eff", 3.2), 1.0, 30.0, "eps_eff")
    segments = int(_bounded_float(params.get("mesh_segments", 4), 2, 64, "mesh_segments"))
    frequency_ghz = _bounded_float(params.get("frequency_ghz", 1.0), .001, 300.0,
                                   "frequency_ghz")
    microstrip = mom.Microstrip(length=length_mm * 1e-3, width=width_mm * 1e-3,
                                height=height_mm * 1e-3, eps_eff=eps_eff,
                                nx=segments, z0_ref=50.0)
    freqs = np.array([frequency_ghz * 1e9], dtype=float)
    s = microstrip.solve_sweep(freqs)
    if s.shape != (1, 2, 2) or not np.isfinite(s).all():
        raise RuntimeError("MoMCraft numerical microstrip solve returned invalid S parameters")
    output = workspace / "microstrip_solver.s2p"
    microstrip.to_touchstone(str(output), freqs, fmt="RI")
    result = workspace / "solver_result.json"
    _write(result, {"schema_version": 1, "solver": "MoMCraft Microstrip",
                    "frequency_hz": float(freqs[0]), "mesh_segments": segments,
                    "geometry_mm": {"length": length_mm, "width": width_mm,
                                    "height": height_mm}, "eps_eff": eps_eff,
                    "s11_magnitude": float(abs(s[0, 0, 0])),
                    "s21_magnitude": float(abs(s[0, 1, 0])),
                    "signoff": False})
    return ([{"kind": "s_parameters", "path": output.name, "solver_output": True},
             {"kind": "solver_result", "path": result.name}],
            [{"name": "momcraft.s11_magnitude", "value": float(abs(s[0, 0, 0])), "unit": "ratio"},
             {"name": "momcraft.s21_magnitude", "value": float(abs(s[0, 1, 0])), "unit": "ratio"}],
            ["Executed the compiled fixed upstream MoM microstrip solver",
             f"Used one frequency and a {segments}-segment mesh to bound cost; no sign-off claim"])


def _cktcraft(root: Path, workspace: Path, task: dict):
    base = root / "CktCraft"
    required = [base / "CMakeLists.txt", base / "src" / "cli" / "main.cpp",
                base / "tests" / "netlists" / "divider.sp"]
    if any(not path.is_file() for path in required):
        raise RuntimeError("CktCraft source/netlist surface is incomplete")
    binary = Path(os.environ["EDACRAFT_CKTCRAFT_BIN"]).resolve()
    if not binary.is_file():
        raise RuntimeError("Pinned CktCraft rfsim binary is missing")
    if _sha256(binary) != os.environ["EDACRAFT_CKTCRAFT_SHA256"]:
        raise RuntimeError("Pinned CktCraft rfsim SHA-256 mismatch")
    supplied = str(task.get("inputs", {}).get("spice_netlist") or "").strip()
    netlist = workspace / "circuit.sp"
    if supplied:
        if len(supplied.encode("utf-8")) > 64 * 1024:
            raise ValueError("SPICE netlist exceeds 64 KiB")
        if re.search(r"(?im)^\s*\.(?:include|lib|control|shell)\b", supplied):
            raise ValueError("External includes and control commands are not allowed")
        if not re.search(r"(?im)^\s*\.end\s*$", supplied):
            raise ValueError("SPICE netlist must end with .end")
        netlist.write_text(supplied + "\n", encoding="utf-8")
    else:
        shutil.copyfile(required[2], netlist)
    completed = subprocess.run([str(binary), str(netlist)], cwd=workspace, text=True,
                               capture_output=True, timeout=30, check=False)
    log_text = completed.stdout + completed.stderr
    log_path = workspace / "simulation.log"
    log_path.write_text(log_text, encoding="utf-8")
    if completed.returncode != 0 or "converged in" not in log_text:
        raise RuntimeError(f"CktCraft .op failed with exit {completed.returncode}")
    values = {name: float(value) for name, value in re.findall(
        r"(?:v|i)\(([^)]+)\)\s*=\s*([+-]?[0-9.]+e[+-][0-9]+)", log_text, re.I
    )}
    if not values:
        raise RuntimeError("CktCraft output did not contain operating-point values")
    result = workspace / "operating_point.json"
    _write(result, {"schema_version": 1, "solver": "rfsim v0.2.0", "analysis": ".op",
                    "converged": True, "reported_values": values, "signoff": False})
    metrics = [{"name": "cktcraft.converged", "value": 1, "unit": "boolean"},
               {"name": "cktcraft.reported_values", "value": len(values), "unit": "count"}]
    for name in ("in", "mid", "v1"):
        if name in values:
            metrics.append({"name": f"cktcraft.{('i' if name == 'v1' else 'v')}_{name}",
                            "value": values[name], "unit": "A" if name == "v1" else "V"})
    return ([{"kind": "simulation_result", "path": result.name},
             {"kind": "simulation_log", "path": log_path.name}],
            metrics,
            ["Executed the compiled fixed upstream rfsim .op solver",
             "Parsed converged values from the submitted bounded SPICE netlist"])


def _snapshot(root: Path, component: str, commit: str, mode: str | None) -> dict:
    return {
        "schema_version": 1,
        "repository": "https://github.com/ephonic/EDACraft",
        "commit": commit,
        "component": component,
        "mode": mode,
        "python": sys.version.split()[0],
        "license": "MIT-like, non-commercial restriction",
        "source_clean": True,
    }


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bounded_float(value, minimum: float, maximum: float, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not minimum <= result <= maximum:
        raise ValueError(f"{name} is outside the supported range")
    return result


def _fail(path: Path, started: str, category: str, message: str) -> int:
    _write(path, {
        "schema_version": 1, "status": "failed", "exit_code": 1,
        "started_at": started, "ended_at": _now(), "metrics": [], "artifacts": [],
        "failure": {"category": category, "message": message}, "provenance": {},
    })
    return 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
