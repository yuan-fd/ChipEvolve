"""Public construction helpers for the ORFS v1 plugin."""

from __future__ import annotations

import hashlib
import os
import platform
import sys
import uuid
from pathlib import Path

from openroad_platform_contracts import PluginManifest, RunRequest, RunStage, TaskSpec

from .toolchain import ToolchainConfig


ORFS_PLUGIN_ID = "orfs"
ORFS_PLUGIN_VERSION = "1.0.0"


def build_orfs_task(
    rtl_path: str | Path,
    *,
    project_id: str,
    design_id: str,
    top: str | None = None,
    clock: str | None = None,
    platform_name: str = "nangate45",
    target_stage: str = "finish",
    clock_period_ns: float = 10.0,
    core_utilization_pct: float = 10.0,
    place_density: float = 0.45,
    or_seed: int = 1,
    minimum_die_size_um: float | None = None,
    stage_timeout_seconds: int = 3600,
    timeout_seconds: int = 7200,
    max_attempts: int = 1,
    task_id: str | None = None,
    labels: dict[str, str] | None = None,
) -> TaskSpec:
    """Create a TaskSpec with an immutable local RTL artifact reference."""

    source = Path(rtl_path).expanduser().resolve()
    if not source.is_file() or source.stat().st_size == 0:
        raise FileNotFoundError(f"RTL input is missing or empty: {source}")
    legacy = RunRequest(
        rtl_path=str(source), top=top, clock=clock,
        clock_period_ns=clock_period_ns, platform=platform_name,
        target_stage=RunStage(target_stage),
        core_utilization_pct=core_utilization_pct,
        place_density=place_density,
        or_seed=or_seed,
        minimum_die_size_um=minimum_die_size_um,
        stage_timeout_seconds=stage_timeout_seconds,
    )
    legacy.validate()
    expected = ["odb", "config", "toolchain_snapshot", "run_result"]
    if target_stage == "finish":
        expected.extend(("def", "netlist", "gds"))
    task = TaskSpec(
        task_id=task_id or f"orfs-{uuid.uuid4().hex}",
        project_id=project_id,
        design_id=design_id,
        plugin_id=ORFS_PLUGIN_ID,
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
            "platform": legacy.platform,
            "target_stage": legacy.target_stage.value,
            "clock_period_ns": legacy.clock_period_ns,
            "core_utilization_pct": legacy.core_utilization_pct,
            "place_density": legacy.place_density,
            "or_seed": legacy.or_seed,
            "minimum_die_size_um": legacy.minimum_die_size_um,
            "stage_timeout_seconds": legacy.stage_timeout_seconds,
        },
        resources={"toolchain_profile": "default"},
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        expected_artifacts=tuple(expected),
        labels=dict(labels or {}),
    )
    task.validate()
    return task


def orfs_plugin_manifest(
    toolchain: ToolchainConfig,
    *,
    python_executable: str | Path = sys.executable,
    default_timeout_seconds: int = 21_600,
) -> PluginManifest:
    """Bind the repository adapter to one explicit immutable toolchain profile."""

    adapter = Path(__file__).with_name("orfs_adapter.py").resolve()
    environment = {
        key: os.environ[key]
        for key in toolchain.inherit_environment
        if key in os.environ
    }
    environment.update(toolchain.environment)
    environment.update({
        "ORFS_ROOT": str(toolchain.orfs_root),
        "OPENROAD_BIN": str(toolchain.openroad_bin),
        "YOSYS_BIN": str(toolchain.yosys_bin),
        "OPENROAD_PLATFORM_TOOLCHAIN_PROFILE": toolchain.name,
    })
    if toolchain.klayout_bin is not None:
        environment["KLAYOUT_BIN"] = str(toolchain.klayout_bin)
    manifest = PluginManifest(
        plugin_id=ORFS_PLUGIN_ID,
        plugin_version=ORFS_PLUGIN_VERSION,
        adapter_entry=(str(Path(python_executable).resolve()), str(adapter)),
        capabilities=("eda.orfs", "eda.rtl_to_gds"),
        supported_arch=(platform.machine(),),
        input_schema={
            "type": "object",
            "required": ["rtl"],
            "properties": {
                "rtl": {
                    "type": "object",
                    "required": ["path", "size_bytes", "sha256"],
                },
                "top": {"type": ["string", "null"]},
                "clock": {"type": ["string", "null"]},
            },
        },
        output_schema={"type": "object", "required": ["status", "artifacts"]},
        required_tools=("make", "git", "openroad", "yosys"),
        default_timeout_seconds=default_timeout_seconds,
        artifact_rules=tuple(
            {"kind": kind, "required": False}
            for kind in (
                "odb", "def", "netlist", "gds", "log", "report", "config",
                "toolchain_snapshot", "run_result", "other",
                "layout_view",
            )
        ),
        environment=environment,
    )
    manifest.validate()
    return manifest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
