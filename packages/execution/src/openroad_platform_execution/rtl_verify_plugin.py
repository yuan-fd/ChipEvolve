"""Plugin contract for isolated RTL compile/lint verification."""

from __future__ import annotations

import hashlib
import os
import platform
import sys
import uuid
from pathlib import Path

from openroad_platform_contracts import PluginManifest, TaskSpec


RTL_VERIFY_PLUGIN_ID = "rtl-verify"
RTL_VERIFY_PLUGIN_VERSION = "1.0.0"


def build_rtl_verify_task(*, project_id: str, design_id: str, rtl_path: str | Path,
                          top: str, verification_id: str, spec_id: str,
                          timeout_seconds: int = 300, task_id: str | None = None,
                          labels: dict[str, str] | None = None) -> TaskSpec:
    source = Path(rtl_path).expanduser().resolve()
    if not source.is_file() or source.stat().st_size == 0:
        raise FileNotFoundError("RTL verification input is missing or empty")
    if not top or not verification_id or not spec_id:
        raise ValueError("top, verification_id and spec_id are required")
    task = TaskSpec(
        task_id=task_id or f"rtl-verify-{uuid.uuid4().hex}", project_id=project_id,
        design_id=design_id, plugin_id=RTL_VERIFY_PLUGIN_ID,
        inputs={"rtl": {"path": str(source), "sha256": _sha256(source),
                          "size_bytes": source.stat().st_size}, "top": top,
                "spec_id": spec_id, "verification_id": verification_id},
        resources={"toolchain_profile": "rtl-verify"}, timeout_seconds=timeout_seconds,
        expected_artifacts=("rtl", "verification_report", "log"), labels=dict(labels or {}),
    )
    task.validate()
    return task


def rtl_verify_plugin_manifest(*, verilator_bin: str | Path, yosys_bin: str | Path,
                               python_executable: str | Path = sys.executable,
                               default_timeout_seconds: int = 600) -> PluginManifest:
    verilator, yosys = Path(verilator_bin).expanduser().resolve(), Path(yosys_bin).expanduser().resolve()
    for name, path in (("Verilator", verilator), ("Yosys", yosys)):
        if not path.is_file():
            raise FileNotFoundError(f"{name} not found: {path}")
    adapter = Path(__file__).with_name("rtl_verify_adapter.py").resolve()
    manifest = PluginManifest(
        plugin_id=RTL_VERIFY_PLUGIN_ID, plugin_version=RTL_VERIFY_PLUGIN_VERSION,
        adapter_entry=(str(Path(python_executable).resolve()), str(adapter)),
        capabilities=("eda.rtl.verify",), supported_arch=(platform.machine(),),
        input_schema={"type": "object", "required": ["rtl", "top", "spec_id", "verification_id"]},
        output_schema={"type": "object", "required": ["status", "artifacts"]},
        required_tools=("verilator", "yosys"), default_timeout_seconds=default_timeout_seconds,
        artifact_rules=(
            {"kind": "rtl", "required": True},
            {"kind": "verification_report", "required": True},
            {"kind": "log", "required": True},
        ),
        environment={"VERILATOR_BIN": str(verilator), "YOSYS_BIN": str(yosys),
                     "PATH": os.pathsep.join((str(verilator.parent), str(yosys.parent), "/usr/bin", "/bin"))},
    )
    manifest.validate()
    return manifest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
