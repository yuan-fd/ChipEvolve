"""Construction helpers for the pinned AgenticPD black-box proposal plugin."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import uuid
from pathlib import Path

from openroad_platform_contracts import PluginManifest, TaskSpec


AGENTICPD_PLUGIN_ID = "agenticpd"
AGENTICPD_PLUGIN_VERSION = "1.0.0"
AGENTICPD_UPSTREAM_COMMIT = "4322a25c1d57bc88d576fd2ce6898a52d30d92c7"


def build_agenticpd_task(
    *, project_id: str, design_id: str, platform_name: str = "nangate45",
    iterations: int = 1, mode: str = "mock", timeout_seconds: int = 300,
    task_id: str | None = None,
) -> TaskSpec:
    if mode not in {"mock", "real"}:
        raise ValueError("mode must be mock or real")
    if not isinstance(iterations, int) or not 1 <= iterations <= 10:
        raise ValueError("iterations must be between 1 and 10")
    task = TaskSpec(
        task_id=task_id or f"agenticpd-{uuid.uuid4().hex}",
        project_id=project_id, design_id=design_id, plugin_id=AGENTICPD_PLUGIN_ID,
        inputs={"platform": platform_name, "design": design_id},
        parameters={"iterations": iterations, "mode": mode},
        resources={"credential_env": "DEEPSEEK_API_KEY" if mode == "real" else None},
        timeout_seconds=timeout_seconds, max_attempts=1,
        expected_artifacts=("experiment_plan", "proposal_evidence", "log"),
        labels={"planner_output_only": "true"},
    )
    task.validate()
    return task


def agenticpd_plugin_manifest(
    source_root: str | Path, *, python_executable: str | Path = sys.executable,
    expected_commit: str = AGENTICPD_UPSTREAM_COMMIT,
    default_timeout_seconds: int = 600,
    credential: str | None = None,
) -> PluginManifest:
    source = Path(source_root).expanduser().resolve()
    python = Path(python_executable).expanduser().absolute()
    if not (source / "main.py").is_file() or not python.is_file():
        raise FileNotFoundError("AgenticPD main.py or Python executable is missing")
    actual = _git(source, "rev-parse", "HEAD")
    if actual != expected_commit:
        raise ValueError(f"AgenticPD source commit mismatch: expected {expected_commit}, got {actual}")
    if _git(source, "status", "--porcelain=v1"):
        raise ValueError("AgenticPD source tree must be clean")
    environment = {
        "AGENTICPD_SOURCE": str(source),
        "AGENTICPD_EXPECTED_COMMIT": expected_commit,
        "PATH": os.pathsep.join((str(python.parent), "/usr/bin", "/bin")),
    }
    if credential:
        environment["DEEPSEEK_API_KEY"] = credential
    adapter = Path(__file__).with_name("agenticpd_adapter.py").resolve()
    manifest = PluginManifest(
        plugin_id=AGENTICPD_PLUGIN_ID, plugin_version=AGENTICPD_PLUGIN_VERSION,
        adapter_entry=(str(python), str(adapter)),
        capabilities=("agent.flow.propose",), supported_arch=(platform.machine(),),
        input_schema={"type": "object", "required": ["platform", "design"]},
        output_schema={"type": "object", "required": ["status", "artifacts"]},
        required_tools=("git", "python"), default_timeout_seconds=default_timeout_seconds,
        artifact_rules=(
            {"kind": "experiment_plan", "required": True},
            {"kind": "proposal_evidence", "required": True},
            {"kind": "log", "required": True},
        ), environment=environment,
    )
    manifest.validate()
    return manifest


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args], check=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if completed.returncode:
        raise ValueError(completed.stderr.strip())
    return completed.stdout.strip()
