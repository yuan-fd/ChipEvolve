"""Pinned, read-only DPLEvolve release-audit plugin construction."""

from __future__ import annotations

import hashlib
import os
import platform
import uuid
from pathlib import Path

from openroad_platform_contracts import PluginManifest, TaskSpec


DPLEVOLVE_PLUGIN_ID = "dplevolve"
DPLEVOLVE_PLUGIN_VERSION = "1.0.0"
DPLEVOLVE_UPSTREAM_COMMIT = "96d8c613d62bf3431083bb5e52c7df8853d5a622"
DPLEVOLVE_LICENSE = "BSD-3-Clause"


def source_tree_digest(root: str | Path) -> tuple[str, int]:
    """Hash stable source content while ignoring interpreter/VCS by-products."""

    source = Path(root).expanduser().resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"DPLEvolve source root is missing: {source}")
    digest = hashlib.sha256()
    count = 0
    for path in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(source)
        if any(part in {".git", "__pycache__"} for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"DPLEvolve source contains a symlink: {relative}")
        if not path.is_file() or path.suffix == ".pyc":
            continue
        payload = path.read_bytes()
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(payload)).encode("ascii"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(payload).hexdigest().encode("ascii"))
        digest.update(b"\n")
        count += 1
    if not count:
        raise ValueError("DPLEvolve source tree is empty")
    return digest.hexdigest(), count


def build_dplevolve_audit_task(
    *, project_id: str, design_id: str = "control-repository",
    timeout_seconds: int = 300, task_id: str | None = None,
) -> TaskSpec:
    """Build a static release-readiness task; it cannot launch EDA/evolution."""

    task = TaskSpec(
        task_id=task_id or f"dplevolve-audit-{uuid.uuid4().hex}",
        project_id=project_id,
        design_id=design_id,
        plugin_id=DPLEVOLVE_PLUGIN_ID,
        inputs={"scope": "fixed-control-repository"},
        parameters={
            "mode": "release-readiness-static",
            "skip_teacher_dry_run": True,
            "allow_eda_execution": False,
            "allow_source_mutation": False,
        },
        resources={"execution_class": "read-only-source-audit"},
        timeout_seconds=timeout_seconds,
        max_attempts=1,
        expected_artifacts=("release_gate_log", "source_lock", "audit_report"),
        labels={
            "runtime_authority": "true",
            "source_mutation": "forbidden",
            "promotion": "human-only",
        },
    )
    task.validate()
    return task


def dplevolve_plugin_manifest(
    source_root: str | Path,
    python_executable: str | Path,
    *,
    expected_commit: str = DPLEVOLVE_UPSTREAM_COMMIT,
    expected_tree_sha256: str | None = None,
    expected_file_count: int | None = None,
    default_timeout_seconds: int = 600,
) -> PluginManifest:
    source = Path(source_root).expanduser().resolve()
    python = Path(python_executable).expanduser().absolute()
    gate = source / "scripts/repo/check_release_readiness.sh"
    license_path = source / "LICENSE"
    if not gate.is_file() or not license_path.is_file() or not python.is_file():
        raise FileNotFoundError("DPLEvolve source, release gate, license, or Python is missing")
    if len(expected_commit) != 40 or any(c not in "0123456789abcdef" for c in expected_commit):
        raise ValueError("DPLEvolve expected commit is invalid")
    actual_digest, actual_count = source_tree_digest(source)
    if expected_tree_sha256 is not None and actual_digest != expected_tree_sha256:
        raise ValueError(
            "DPLEvolve source tree mismatch: "
            f"expected {expected_tree_sha256}, got {actual_digest}"
        )
    if expected_file_count is not None and actual_count != expected_file_count:
        raise ValueError(
            f"DPLEvolve source file count mismatch: expected {expected_file_count}, "
            f"got {actual_count}"
        )
    adapter = Path(__file__).with_name("dplevolve_adapter.py").resolve()
    manifest = PluginManifest(
        plugin_id=DPLEVOLVE_PLUGIN_ID,
        plugin_version=DPLEVOLVE_PLUGIN_VERSION,
        adapter_entry=(str(python), str(adapter)),
        capabilities=("eda.tool-evolve.audit", "eda.whitebox.proposal"),
        supported_arch=(platform.machine(),),
        input_schema={"type": "object", "required": ["scope"]},
        output_schema={"type": "object", "required": ["status", "artifacts"]},
        required_tools=("bash", "git", "python"),
        default_timeout_seconds=default_timeout_seconds,
        artifact_rules=tuple(
            {"kind": kind, "required": True}
            for kind in ("release_gate_log", "source_lock", "audit_report")
        ),
        environment={
            "DPLEVOLVE_SOURCE": str(source),
            "DPLEVOLVE_EXPECTED_COMMIT": expected_commit,
            "DPLEVOLVE_EXPECTED_TREE_SHA256": expected_tree_sha256 or actual_digest,
            "DPLEVOLVE_EXPECTED_FILE_COUNT": str(
                expected_file_count if expected_file_count is not None else actual_count
            ),
            "DPLEVOLVE_LICENSE": DPLEVOLVE_LICENSE,
            "DPLEVOLVE_PYTHON": str(python),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PATH": os.pathsep.join((str(python.parent), "/usr/bin", "/bin")),
        },
    )
    manifest.validate()
    return manifest
