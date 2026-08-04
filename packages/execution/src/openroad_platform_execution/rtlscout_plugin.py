"""Public construction helpers for the pinned RTLScout black-box plugin."""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import uuid
from pathlib import Path

from openroad_platform_contracts import PluginManifest, TaskSpec


RTLSCOUT_PLUGIN_ID = "rtlscout"
RTLSCOUT_PLUGIN_VERSION = "1.0.0"
RTLSCOUT_UPSTREAM_COMMIT = "87a00edf6b9208f657dd9ffdda170004024c08ae"
RTLSCOUT_PROVIDERS = frozenset({"fake", "anthropic", "deepinfra", "openrouter"})
RTLSCOUT_CREDENTIALS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "deepinfra": "DEEPINFRA_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def build_rtlscout_task(
    *,
    project_id: str,
    design_id: str,
    benchmark: str,
    model: str,
    max_steps: int = 20,
    cost_metric: str = "transistors",
    timeout_seconds: int = 1800,
    task_id: str | None = None,
    labels: dict[str, str] | None = None,
) -> TaskSpec:
    """Build a durable request without ever persisting an API credential."""

    if not benchmark or any(part in benchmark for part in ("..", "\\", "\x00")):
        raise ValueError("benchmark must be a repository-local benchmark name")
    if ":" not in model:
        raise ValueError("model must use provider:model syntax")
    provider, model_name = model.split(":", 1)
    if provider not in RTLSCOUT_PROVIDERS or not model_name:
        raise ValueError(f"Unsupported RTLScout model provider: {provider!r}")
    if not isinstance(max_steps, int) or not 1 <= max_steps <= 100:
        raise ValueError("max_steps must be between 1 and 100")
    if not cost_metric or not cost_metric.replace("_", "").isalnum():
        raise ValueError("Invalid cost_metric")
    task = TaskSpec(
        task_id=task_id or f"rtlscout-{uuid.uuid4().hex}",
        project_id=project_id,
        design_id=design_id,
        plugin_id=RTLSCOUT_PLUGIN_ID,
        inputs={"benchmark": benchmark},
        parameters={
            "model": model,
            "provider": provider,
            "max_steps": max_steps,
            "cost_metric": cost_metric,
        },
        resources={"credential_env": RTLSCOUT_CREDENTIALS.get(provider)},
        timeout_seconds=timeout_seconds,
        max_attempts=1,
        expected_artifacts=("rtl", "rtlscout_result", "report"),
        labels=dict(labels or {}),
    )
    task.validate()
    return task


def rtlscout_plugin_manifest(
    source_root: str | Path,
    python_executable: str | Path,
    *,
    verilator_bin: str | Path,
    yosys_bin: str | Path,
    expected_commit: str = RTLSCOUT_UPSTREAM_COMMIT,
    default_timeout_seconds: int = 3600,
    credential_environment: dict[str, str] | None = None,
) -> PluginManifest:
    """Bind one clean upstream commit and explicit EDA/Python executables."""

    source = Path(source_root).expanduser().resolve()
    # Preserve a virtualenv's interpreter symlink. Resolving it would silently
    # bypass that environment and lose its site-packages.
    python = Path(python_executable).expanduser().absolute()
    verilator = Path(verilator_bin).expanduser().resolve()
    yosys = Path(yosys_bin).expanduser().resolve()
    for name, path in (
        ("RTLScout source", source / "run_benchmark.py"),
        ("Python", python),
        ("Verilator", verilator),
        ("Yosys", yosys),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"{name} not found: {path}")
    actual = _git(source, "rev-parse", "HEAD")
    if actual != expected_commit:
        raise ValueError(
            f"RTLScout source commit mismatch: expected {expected_commit}, got {actual}"
        )
    if _git(source, "status", "--porcelain=v1"):
        raise ValueError("RTLScout source tree must be clean")
    spire = source / "deps" / "spire-hdl"
    if not spire.is_dir() or not any(spire.iterdir()):
        raise ValueError("RTLScout deps/spire-hdl fixed submodule is not initialized")

    adapter = Path(__file__).with_name("rtlscout_adapter.py").resolve()
    path_parts = tuple(dict.fromkeys((
        str(verilator.parent), str(yosys.parent), "/usr/bin", "/bin",
    )))
    environment = {
        "RTLSCOUT_SOURCE": str(source),
        "RTLSCOUT_PYTHON": str(python),
        "RTLSCOUT_EXPECTED_COMMIT": expected_commit,
        "PATH": os.pathsep.join(path_parts),
    }
    for key, value in (credential_environment or {}).items():
        if key not in set(RTLSCOUT_CREDENTIALS.values()):
            raise ValueError(f"Credential environment variable is not allowlisted: {key}")
        if value:
            environment[key] = value
    manifest = PluginManifest(
        plugin_id=RTLSCOUT_PLUGIN_ID,
        plugin_version=RTLSCOUT_PLUGIN_VERSION,
        adapter_entry=(str(python), str(adapter)),
        capabilities=("agent.rtl.generate", "agent.rtl.optimize"),
        supported_arch=(platform.machine(),),
        input_schema={
            "type": "object",
            "required": ["benchmark"],
            "properties": {"benchmark": {"type": "string"}},
        },
        output_schema={"type": "object", "required": ["status", "artifacts"]},
        required_tools=("verilator", "yosys", "git"),
        default_timeout_seconds=default_timeout_seconds,
        artifact_rules=(
            {"kind": "rtl", "required": True},
            {"kind": "rtlscout_result", "required": True},
            {"kind": "report", "required": True},
            {"kind": "log", "required": False},
        ),
        environment=environment,
    )
    manifest.validate()
    return manifest


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args], check=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if completed.returncode != 0:
        raise ValueError(f"Cannot inspect RTLScout source: {completed.stderr.strip()}")
    return completed.stdout.strip()


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
