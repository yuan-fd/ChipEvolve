"""Bounded formal RTL gate using a frozen assertion harness and Yosys SAT."""
from __future__ import annotations
import hashlib
import os
import platform
import sys
import uuid
from pathlib import Path
from openroad_platform_contracts import PluginManifest, TaskSpec

RTL_FORMAL_PLUGIN_ID = "rtl-formal"
RTL_FORMAL_PLUGIN_VERSION = "1.0.0"

def build_rtl_formal_task(*, project_id: str, design_id: str, rtl_path: str | Path,
                          property_path: str | Path, property_top: str, spec_id: str,
                          verification_id: str, depth: int = 1, timeout_seconds: int = 300,
                          task_id: str | None = None, labels: dict[str, str] | None = None) -> TaskSpec:
    rtl, prop = Path(rtl_path).resolve(), Path(property_path).resolve()
    if not 1 <= depth <= 64: raise ValueError("formal depth must be in [1, 64]")
    for name, path in (("RTL", rtl), ("property", prop)):
        if not path.is_file() or not path.stat().st_size: raise FileNotFoundError(f"Formal {name} is missing or empty")
    task = TaskSpec(task_id=task_id or f"rtl-formal-{uuid.uuid4().hex}", project_id=project_id, design_id=design_id,
        plugin_id=RTL_FORMAL_PLUGIN_ID, inputs={"rtl": _input(rtl), "property": _input(prop), "property_top": property_top, "spec_id": spec_id, "verification_id": verification_id},
        parameters={"depth": depth}, resources={"toolchain_profile": "rtl-formal-yosys-sat"}, timeout_seconds=timeout_seconds,
        expected_artifacts=("formal_report", "log"), labels=dict(labels or {})); task.validate(); return task

def rtl_formal_plugin_manifest(*, yosys_bin: str | Path, python_executable: str | Path = sys.executable, default_timeout_seconds: int = 600) -> PluginManifest:
    yosys = Path(yosys_bin).resolve()
    if not yosys.is_file(): raise FileNotFoundError(f"Yosys not found: {yosys}")
    adapter = Path(__file__).with_name("rtl_formal_adapter.py").resolve()
    result = PluginManifest(plugin_id=RTL_FORMAL_PLUGIN_ID, plugin_version=RTL_FORMAL_PLUGIN_VERSION, adapter_entry=(str(Path(python_executable).resolve()), str(adapter)), capabilities=("eda.rtl.formal",), supported_arch=(platform.machine(),), input_schema={"type":"object","required":["rtl","property","property_top","spec_id","verification_id"]}, output_schema={"type":"object","required":["status","artifacts"]}, required_tools=("yosys",), default_timeout_seconds=default_timeout_seconds, artifact_rules=({"kind":"formal_report","required":True},{"kind":"log","required":True}), environment={"YOSYS_BIN":str(yosys),"PATH":os.pathsep.join((str(yosys.parent),"/usr/bin","/bin"))}); result.validate(); return result

def _input(path: Path) -> dict[str, object]: return {"path":str(path),"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"size_bytes":path.stat().st_size}
