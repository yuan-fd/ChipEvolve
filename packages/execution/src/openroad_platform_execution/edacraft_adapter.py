#!/usr/bin/env python3
"""Bounded low-cost adapter for five EDACraft extension components."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
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
        generated, metrics, claims = _run_smoke(slug, root, workspace)
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
                "full_solver_executed": False,
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


def _run_smoke(slug: str, root: Path, workspace: Path):
    if slug == "rtlcraft":
        return _rtlcraft(root, workspace)
    if slug == "edacode":
        return _edacode(root)
    if slug == "tcadcraft":
        return _tcadcraft(root, workspace)
    if slug == "momcraft":
        return _momcraft(root, workspace)
    if slug == "cktcraft":
        return _cktcraft(root)
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


def _edacode(root: Path):
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
    return ([], [{"name": "edacode.audited_surfaces", "value": len(required), "unit": "count"}],
            ["Provider, agent, VS Code server, and tool surfaces exist",
             "Platform adapter intentionally executes none of the upstream shell/file tools"])


def _tcadcraft(root: Path, workspace: Path):
    import numpy as np

    # Load the leaf module directly: importing ``tcad`` eagerly loads the full
    # solver stack (including optional SciPy), which is outside this smoke.
    module_path = root / "TCADCraft" / "tcad" / "geometry" / "shapes.py"
    spec = importlib.util.spec_from_file_location("edacraft_tcad_shapes", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load upstream TCADCraft geometry module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    box = module.Box(0.0, 10e-9, 0.0, 5e-9, 0.0, 3e-9)
    mask = box.contains(
        np.array([5e-9, 12e-9]), np.array([2e-9, 2e-9]), np.array([1e-9, 1e-9])
    )
    geometry = workspace / "device_geometry.json"
    _write(geometry, {"primitive": "Box", "bbox_m": box.bbox(), "inside": mask.tolist()})
    return ([{"kind": "device_geometry", "path": geometry.name}],
            [{"name": "tcadcraft.geometry_points", "value": 2, "unit": "count"}],
            ["Imported fixed upstream TCAD geometry code", "Evaluated deterministic 3D containment"])


def _momcraft(root: Path, workspace: Path):
    import numpy as np

    module_path = root / "MoMCraft" / "py" / "mom" / "touchstone.py"
    spec = importlib.util.spec_from_file_location("edacraft_mom_touchstone", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load upstream MoMCraft Touchstone module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # A single-point round trip avoids presenting synthetic data as a sweep.
    # Full frequency sweeps belong to the compiled MoM solver acceptance.
    freqs = np.array([1e9])
    s = np.zeros((1, 2, 2), dtype=complex)
    s[:, 0, 0] = 0.05
    s[:, 1, 1] = 0.05
    s[:, 1, 0] = np.array([0.91])
    s[:, 0, 1] = s[:, 1, 0]
    output = workspace / "microstrip_contract.s2p"
    module.write_touchstone(output, freqs, s, comments=["P17 low-cost I/O smoke; not solver output"])
    read_freqs, read_s, _ = module.read_touchstone(output)
    if read_freqs.shape != (1,) or read_s.shape != (1, 2, 2):
        raise RuntimeError("MoMCraft Touchstone round trip failed")
    return ([{"kind": "s_parameters", "path": output.name, "solver_output": False}],
            [{"name": "momcraft.touchstone_points", "value": 1, "unit": "count"}],
            ["Executed fixed upstream Touchstone writer and reader", "No EM solve was claimed"])


def _cktcraft(root: Path):
    base = root / "CktCraft"
    required = [base / "CMakeLists.txt", base / "src" / "cli" / "main.cpp",
                base / "tests" / "netlists" / "divider.sp"]
    if any(not path.is_file() for path in required):
        raise RuntimeError("CktCraft source/netlist surface is incomplete")
    cmake_text = required[0].read_text(encoding="utf-8", errors="replace")
    readme = (base / "README.md").read_text(encoding="utf-8", errors="replace")
    if "RFSIM_BUILD_TESTS" not in cmake_text or "v0.2.0" not in readme:
        raise RuntimeError("CktCraft fixed source contract is not recognized")
    return ([], [{"name": "cktcraft.audited_inputs", "value": len(required), "unit": "count"}],
            ["Validated v0.2 source, CLI, and resistor-divider netlist surfaces",
             "No SPICE solver execution was claimed"])


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
