"""Pinned, frozen-testbench RTL simulation plugin.

The testbench is supplied as a content-addressed input, never authored by the
candidate.  This makes a simulation pass usable as a functional gate without
granting an agent a shell surface.
"""
from __future__ import annotations

import hashlib
import os
import platform
import sys
import uuid
from pathlib import Path

from openroad_platform_contracts import PluginManifest, TaskSpec

RTL_SIM_PLUGIN_ID = "rtl-sim"
RTL_SIM_PLUGIN_VERSION = "1.0.0"


def build_rtl_sim_task(*, project_id: str, design_id: str, rtl_path: str | Path,
                       testbench_path: str | Path, top: str, verification_id: str,
                       spec_id: str, timeout_seconds: int = 300,
                       task_id: str | None = None, labels: dict[str, str] | None = None) -> TaskSpec:
    rtl, testbench = Path(rtl_path).expanduser().resolve(), Path(testbench_path).expanduser().resolve()
    for name, path in (("RTL", rtl), ("testbench", testbench)):
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(f"Simulation {name} input is missing or empty")
    task = TaskSpec(
        task_id=task_id or f"rtl-sim-{uuid.uuid4().hex}", project_id=project_id,
        design_id=design_id, plugin_id=RTL_SIM_PLUGIN_ID,
        inputs={"rtl": _input(rtl), "testbench": _input(testbench), "top": top,
                "spec_id": spec_id, "verification_id": verification_id},
        resources={"toolchain_profile": "rtl-sim"}, timeout_seconds=timeout_seconds,
        expected_artifacts=("simulation_report", "log"), labels=dict(labels or {}),
    )
    task.validate(); return task


def rtl_sim_plugin_manifest(*, iverilog_bin: str | Path, vvp_bin: str | Path,
                            python_executable: str | Path = sys.executable,
                            default_timeout_seconds: int = 600) -> PluginManifest:
    iverilog, vvp = Path(iverilog_bin).expanduser().resolve(), Path(vvp_bin).expanduser().resolve()
    for name, path in (("Icarus Verilog", iverilog), ("vvp", vvp)):
        if not path.is_file():
            raise FileNotFoundError(f"{name} not found: {path}")
    adapter = Path(__file__).with_name("rtl_sim_adapter.py").resolve()
    manifest = PluginManifest(
        plugin_id=RTL_SIM_PLUGIN_ID, plugin_version=RTL_SIM_PLUGIN_VERSION,
        adapter_entry=(str(Path(python_executable).resolve()), str(adapter)),
        capabilities=("eda.rtl.simulate",), supported_arch=(platform.machine(),),
        input_schema={"type": "object", "required": ["rtl", "testbench", "top", "spec_id", "verification_id"]},
        output_schema={"type": "object", "required": ["status", "artifacts"]},
        required_tools=("iverilog", "vvp"), default_timeout_seconds=default_timeout_seconds,
        artifact_rules=({"kind": "simulation_report", "required": True}, {"kind": "log", "required": True}),
        environment={"IVERILOG_BIN": str(iverilog), "VVP_BIN": str(vvp),
                     "PATH": os.pathsep.join((str(iverilog.parent), str(vvp.parent), "/usr/bin", "/bin"))},
    )
    manifest.validate(); return manifest


def _input(path: Path) -> dict[str, object]:
    return {"path": str(path), "sha256": _sha256(path), "size_bytes": path.stat().st_size}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
