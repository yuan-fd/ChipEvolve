"""Pinned TaiWei-Pin-3D black-box plugin construction.

The adapter is generalised beyond the original single gcd acceptance case:
any official TaiWei case (gcd / ibex / aes / ariane133 / bp_quad / jpeg /
swerv_wrapper) and any supported 3D platform (asap7_3D, nangate45_3D,
asap7_nangate45_3D) can be selected, and engine-native flow parameters are
carried through task.parameters into the engine environment.
"""

from __future__ import annotations

import math
import os
import platform
import re
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

# Official TaiWei cases that ship a complete ord flow under test/<tech>/<case>/.
TAIWEI_OFFICIAL_CASES = ("gcd", "ibex", "aes", "ariane133", "bp_quad", "jpeg",
                         "swerv_wrapper")
TAIWEI_3D_PLATFORMS = ("asap7_3D", "nangate45_3D", "asap7_nangate45_3D")
CASE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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
                       tech: str = "asap7_3D",
                       registered_design_id: str | None = None,
                       rtl: dict | None = None,
                       clock: str | None = None,
                       clock_period_ns: float | None = None,
                       parameters: dict | None = None,
                       timeout_seconds: int = 21600,
                       task_id: str | None = None) -> TaskSpec:
    """Build a TaiWei 3D TaskSpec for an official case and 3D platform.

    ``design_id`` selects the engine case (module top name). ``tech`` selects
    the 3D platform. ``rtl`` optionally carries a platform-registered RTL
    artifact reference ({"path", "size_bytes", "sha256"}) for dynamic cases
    not shipped by the engine; official cases keep their pinned engine RTL.
    ``parameters`` carries engine-native flow knobs surfaced by the Web layer
    (core_utilization_pct, num_cores, cts_layer, outer_iterations,
    skip_2d_part, pin3d_allow_net_flow, pin3d_split_net_flow, abc_area,
    start_from). gcd + asap7_3D remains the fully validated default.
    """
    if tech not in TAIWEI_3D_PLATFORMS:
        raise ValueError(
            f"Unsupported TaiWei 3D platform {tech!r}; choose from "
            + ", ".join(TAIWEI_3D_PLATFORMS))
    if not CASE_RE.fullmatch(design_id):
        raise ValueError(f"Invalid TaiWei case name: {design_id!r}")
    if rtl is not None:
        if not isinstance(rtl, dict) or not all(
                key in rtl for key in ("path", "size_bytes", "sha256")):
            raise ValueError("rtl must carry path/size_bytes/sha256")
    inputs: dict = {"flow": "ord", "tech": tech, "case": design_id}
    if rtl is not None:
        inputs["rtl"] = rtl
    if clock:
        inputs["clock"] = clock
    if clock_period_ns is not None:
        if (isinstance(clock_period_ns, bool)
                or not isinstance(clock_period_ns, (int, float))
                or not math.isfinite(clock_period_ns)
                or clock_period_ns <= 0):
            raise ValueError("clock_period_ns must be a positive finite number")
        inputs["clock_period_ns"] = float(clock_period_ns)
    allowed = _validated_parameters(parameters or {})
    task = TaskSpec(
        task_id=task_id or f"taiwei-{uuid.uuid4().hex}",
        project_id=project_id, design_id=registered_design_id or design_id,
        plugin_id=TAIWEI_PLUGIN_ID,
        inputs=inputs,
        parameters=allowed,
        resources={"toolchain_profile": "taiwei-official-3d"},
        timeout_seconds=timeout_seconds, max_attempts=1,
        expected_artifacts=("three_d_eval", "three_d_summary", "gds", "def", "odb", "netlist",
                            "toolchain_snapshot", "log"),
        labels={"real_3d_required": "true", "fixed_case": design_id,
                "fixed_tech": tech},
    )
    task.validate()
    return task


def _validated_parameters(parameters: dict) -> dict:
    """Allowlist engine-native flow knobs and coerce their types."""
    if not isinstance(parameters, dict):
        raise ValueError("TaiWei parameters must be a mapping")
    allowed: dict = {}
    if "core_utilization_pct" in parameters:
        value = parameters["core_utilization_pct"]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not 1 <= value <= 100):
            raise ValueError("core_utilization_pct must be between 1 and 100")
        allowed["core_utilization_pct"] = int(value)
    if "num_cores" in parameters:
        value = parameters["num_cores"]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 256:
            raise ValueError("num_cores must be between 1 and 256")
        allowed["num_cores"] = value
    if "cts_layer" in parameters:
        value = str(parameters["cts_layer"])
        if value not in ("bottom", "upper"):
            raise ValueError("cts_layer must be 'bottom' or 'upper'")
        allowed["cts_layer"] = value
    if "outer_iterations" in parameters:
        value = parameters["outer_iterations"]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 16:
            raise ValueError("outer_iterations must be between 1 and 16")
        allowed["outer_iterations"] = value
    for key in ("skip_2d_part", "pin3d_allow_net_flow",
                "pin3d_split_net_flow", "abc_area"):
        if key in parameters:
            if not isinstance(parameters[key], bool):
                raise ValueError(f"{key} must be a boolean")
            allowed[key] = parameters[key]
    if "start_from" in parameters:
        value = str(parameters["start_from"]).strip()
        if value:
            allowed["start_from"] = value
    return allowed


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
