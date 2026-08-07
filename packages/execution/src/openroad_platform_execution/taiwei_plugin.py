"""Pinned TaiWei-Pin-3D black-box plugin construction."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from openroad_platform_contracts import PluginManifest, TaskSpec


TAIWEI_PLUGIN_ID = "taiwei-pin-3d"
TAIWEI_PLUGIN_VERSION = "1.0.0"
TAIWEI_UPSTREAM_COMMIT = "db20136711ed8c0cdfed67a6123d059875764abd"
TAIWEI_ORFS_COMMIT = "568eb04da9173695d6bfc1b10ba868e0b6b8a9fa"
TAIWEI_OPENROAD_COMMIT = "305d3ba2ddfd00591924cc586ad408179f566afe"


@dataclass(frozen=True)
class TaiWeiToolchainProfile:
    orfs_root: Path
    openroad_bin: Path
    yosys_bin: Path
    orfs_commit: str = TAIWEI_ORFS_COMMIT
    openroad_commit: str = TAIWEI_OPENROAD_COMMIT
    runtime_library_paths: tuple[Path, ...] = ()

    def validate(self) -> None:
        root = self.orfs_root.expanduser().resolve()
        if _git(root, "rev-parse", "HEAD") != self.orfs_commit:
            raise ValueError("TaiWei ORFS-Research commit mismatch")
        openroad_source = root / "tools/OpenROAD"
        if _git(openroad_source, "rev-parse", "HEAD") != self.openroad_commit:
            raise ValueError("TaiWei bundled OpenROAD commit mismatch")
        # The isolated ORFS tree contains large untracked build/install outputs.
        # They are version-locked separately; source integrity is determined by
        # the pinned commit plus staged and unstaged changes to tracked files.
        if _git(root, "status", "--porcelain=v1", "--untracked-files=no"):
            raise ValueError("TaiWei ORFS-Research tracked source must be clean")
        for name, path in (("OpenROAD", self.openroad_bin), ("Yosys", self.yosys_bin)):
            if not path.expanduser().resolve().is_file():
                raise FileNotFoundError(f"TaiWei {name} binary is missing: {path}")
        for path in self.runtime_library_paths:
            if not path.expanduser().resolve().is_dir():
                raise FileNotFoundError(f"TaiWei runtime library directory is missing: {path}")


def build_taiwei_task(*, project_id: str, design_id: str = "gcd",
                       registered_design_id: str | None = None,
                       timeout_seconds: int = 21600,
                       task_id: str | None = None) -> TaskSpec:
    if design_id != "gcd":
        raise ValueError("TaiWei v1 only permits the gcd acceptance case")
    task = TaskSpec(
        task_id=task_id or f"taiwei-{uuid.uuid4().hex}",
        project_id=project_id, design_id=registered_design_id or design_id,
        plugin_id=TAIWEI_PLUGIN_ID,
        inputs={"flow": "ord", "tech": "asap7_3D", "case": "gcd"},
        resources={"toolchain_profile": "taiwei-official-3d"},
        timeout_seconds=timeout_seconds, max_attempts=1,
        expected_artifacts=("three_d_eval", "three_d_summary", "gds", "def", "odb", "netlist",
                            "toolchain_snapshot", "log"),
        labels={"real_3d_required": "true", "fixed_case": design_id},
    )
    task.validate()
    return task


def taiwei_plugin_manifest(source_root: str | Path, profile: TaiWeiToolchainProfile,
                            *, python_executable: str | Path = sys.executable,
                            expected_commit: str = TAIWEI_UPSTREAM_COMMIT,
                            default_timeout_seconds: int = 21600,
                            num_cores: int = 8) -> PluginManifest:
    source = Path(source_root).expanduser().resolve()
    python = Path(python_executable).expanduser().absolute()
    if not (source / "run_experiments.py").is_file() or not python.is_file():
        raise FileNotFoundError("TaiWei source entry or Python is missing")
    if _git(source, "rev-parse", "HEAD") != expected_commit:
        raise ValueError("TaiWei source commit mismatch")
    if _git(source, "status", "--porcelain=v1"):
        raise ValueError("TaiWei source tree must be clean")
    if not 1 <= num_cores <= 256:
        raise ValueError("TaiWei num_cores must be between 1 and 256")
    profile.validate()
    adapter = Path(__file__).with_name("taiwei_adapter.py").resolve()
    environment = {
        "TAIWEI_SOURCE": str(source), "TAIWEI_EXPECTED_COMMIT": expected_commit,
        "TAIWEI_ORFS_ROOT": str(profile.orfs_root.resolve()),
        "TAIWEI_ORFS_COMMIT": profile.orfs_commit,
        "TAIWEI_OPENROAD_COMMIT": profile.openroad_commit,
        "OPENROAD_EXE": str(profile.openroad_bin.resolve()),
        "YOSYS_EXE": str(profile.yosys_bin.resolve()),
        "TAIWEI_NUM_CORES": str(num_cores),
        "PATH": os.pathsep.join((str(profile.openroad_bin.resolve().parent),
                                 str(profile.yosys_bin.resolve().parent), "/usr/bin", "/bin")),
    }
    if profile.runtime_library_paths:
        environment["LD_LIBRARY_PATH"] = os.pathsep.join(
            str(path.expanduser().resolve()) for path in profile.runtime_library_paths
        )
    manifest = PluginManifest(
        plugin_id=TAIWEI_PLUGIN_ID, plugin_version=TAIWEI_PLUGIN_VERSION,
        adapter_entry=(str(python), str(adapter)), capabilities=("eda.3d.pin3d",),
        supported_arch=(platform.machine(),), input_schema={"type": "object"},
        output_schema={"type": "object"}, required_tools=("git", "bash", "make"),
        default_timeout_seconds=default_timeout_seconds,
        artifact_rules=tuple({"kind": kind, "required": kind in {
                                 "three_d_eval", "three_d_summary", "gds", "def", "odb",
                                 "netlist", "toolchain_snapshot", "log"}}
                             for kind in ("three_d_eval", "three_d_summary", "gds", "def",
                                          "odb", "netlist", "sdc", "spef", "three_d_report",
                                          "toolchain_snapshot", "log", "layout_view",
                                          "three_d_view")),
        environment=environment,
    )
    manifest.validate()
    return manifest


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(["git", "-C", str(root), *args], check=False,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if completed.returncode:
        raise ValueError(f"Cannot inspect fixed source {root}: {completed.stderr.strip()}")
    return completed.stdout.strip()
