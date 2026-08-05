"""Pinned EDACraft ImplCraft script-generation plugin construction."""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import uuid
from pathlib import Path

from openroad_platform_contracts import PluginManifest, TaskSpec


IMPLCRAFT_PLUGIN_ID = "edacraft-implcraft"
IMPLCRAFT_PLUGIN_VERSION = "1.0.0"
IMPLCRAFT_UPSTREAM_COMMIT = "739eee0f3ced8fc3cbb6f01b6cc89414758fd898"


def build_implcraft_task(
    rtl_path: str | Path,
    *,
    project_id: str,
    design_id: str,
    top: str,
    clock: str = "clk",
    clock_period_ns: float = 10.0,
    tool_chain: str = "synopsys",
    stop_at: str = "floorplan",
    timeout_seconds: int = 300,
    task_id: str | None = None,
) -> TaskSpec:
    """Build a non-commercial ImplCraft dry-run request with immutable RTL."""

    source = Path(rtl_path).expanduser().resolve()
    if not source.is_file() or source.stat().st_size == 0:
        raise FileNotFoundError(f"ImplCraft RTL is missing or empty: {source}")
    if tool_chain not in {"synopsys", "cadence"}:
        raise ValueError("ImplCraft tool_chain must be synopsys or cadence")
    if stop_at not in {"synthesis", "create_lib", "floorplan", "placement"}:
        raise ValueError("ImplCraft v1 stop_at is outside the validated dry-run stages")
    if not 0.01 <= float(clock_period_ns) <= 1000:
        raise ValueError("ImplCraft clock period is outside policy")
    task = TaskSpec(
        task_id=task_id or f"implcraft-{uuid.uuid4().hex}",
        project_id=project_id,
        design_id=design_id,
        plugin_id=IMPLCRAFT_PLUGIN_ID,
        inputs={
            "rtl": {
                "path": str(source),
                "size_bytes": source.stat().st_size,
                "sha256": _sha256(source),
            },
            "top": top,
            "clock": clock,
        },
        parameters={
            "mode": "dry-run",
            "tool_chain": tool_chain,
            "stop_at": stop_at,
            "clock_period_ns": float(clock_period_ns),
        },
        resources={"toolchain_profile": "edacraft-implcraft-scriptgen"},
        timeout_seconds=timeout_seconds,
        max_attempts=1,
        expected_artifacts=(
            "implcraft_config", "implcraft_state", "eda_script",
            "report", "toolchain_snapshot", "log",
        ),
        labels={
            "execution_class": "script-generation-only",
            "commercial_eda_executed": "false",
        },
    )
    task.validate()
    return task


def implcraft_plugin_manifest(
    source_root: str | Path,
    python_executable: str | Path,
    *,
    expected_commit: str = IMPLCRAFT_UPSTREAM_COMMIT,
    default_timeout_seconds: int = 600,
) -> PluginManifest:
    source = Path(source_root).expanduser().resolve()
    implcraft = source / "ImplCraft"
    python = Path(python_executable).expanduser().absolute()
    if not (implcraft / "src/run_flow.py").is_file() or not python.is_file():
        raise FileNotFoundError("EDACraft ImplCraft source or Python is missing")
    actual = _git(source, "rev-parse", "HEAD")
    if actual != expected_commit:
        raise ValueError(
            f"EDACraft source commit mismatch: expected {expected_commit}, got {actual}"
        )
    if _git(source, "status", "--porcelain=v1"):
        raise ValueError("EDACraft source tree must be clean")
    adapter = Path(__file__).with_name("implcraft_adapter.py").resolve()
    manifest = PluginManifest(
        plugin_id=IMPLCRAFT_PLUGIN_ID,
        plugin_version=IMPLCRAFT_PLUGIN_VERSION,
        adapter_entry=(str(python), str(adapter)),
        capabilities=("eda.implcraft.scriptgen", "eda.backend.plan"),
        supported_arch=(platform.machine(),),
        input_schema={"type": "object", "required": ["rtl", "top", "clock"]},
        output_schema={"type": "object", "required": ["status", "artifacts"]},
        required_tools=("git", "python"),
        default_timeout_seconds=default_timeout_seconds,
        artifact_rules=tuple(
            {"kind": kind, "required": True}
            for kind in (
                "implcraft_config", "implcraft_state", "eda_script",
                "report", "toolchain_snapshot", "log",
            )
        ),
        environment={
            "IMPLCRAFT_SOURCE": str(implcraft),
            "EDACRAFT_ROOT": str(source),
            "EDACRAFT_EXPECTED_COMMIT": expected_commit,
            "IMPLCRAFT_PYTHON": str(python),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PATH": os.pathsep.join((str(python.parent), "/usr/bin", "/bin")),
        },
    )
    manifest.validate()
    return manifest


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args], check=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if completed.returncode:
        raise ValueError(f"Cannot inspect EDACraft source: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
