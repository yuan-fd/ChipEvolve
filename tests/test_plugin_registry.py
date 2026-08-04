from __future__ import annotations

import json

import pytest

from openroad_platform_contracts import PluginManifest
from openroad_platform_execution.registry import PluginRegistry


def manifest(version: str = "1.0.0") -> PluginManifest:
    return PluginManifest(
        plugin_id="echo",
        plugin_version=version,
        adapter_entry=("python3", "echo_adapter.py"),
        capabilities=("test.echo",),
        supported_arch=("aarch64", "x86_64"),
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )


def test_registry_resolves_exact_version_capability_and_architecture():
    registry = PluginRegistry()
    registry.register(manifest())

    resolved = registry.resolve(
        "echo", version="1.0.0", capability="test.echo", arch="aarch64"
    )
    assert resolved.plugin_version == "1.0.0"

    with pytest.raises(LookupError, match="capability"):
        registry.resolve("echo", capability="eda.orfs", arch="aarch64")
    with pytest.raises(LookupError, match="architecture"):
        registry.resolve("echo", capability="test.echo", arch="riscv64")


def test_registry_rejects_duplicate_identity():
    registry = PluginRegistry([manifest()])
    with pytest.raises(ValueError, match="already registered"):
        registry.register(manifest())


def test_registry_loads_versioned_manifest_files(tmp_path):
    path = tmp_path / "echo.plugin.json"
    path.write_text(json.dumps(manifest().to_dict()))
    registry = PluginRegistry.from_directory(tmp_path)
    resolved = registry.resolve("echo", arch="x86_64")
    assert resolved.plugin_version == "1.0.0"

    bad = tmp_path / "bad.plugin.json"
    payload = manifest("2.0.0").to_dict()
    payload["schema_version"] = 99
    bad.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="schema_version"):
        PluginRegistry.from_directory(tmp_path)


def test_registry_resolves_explicit_relative_entry_against_manifest_directory(tmp_path):
    path = tmp_path / "echo.plugin.json"
    payload = manifest().to_dict()
    payload["adapter_entry"] = ["python3", "./echo_adapter.py"]
    path.write_text(json.dumps(payload))

    resolved = PluginRegistry.from_directory(tmp_path).resolve("echo", arch="aarch64")

    assert resolved.adapter_entry == (
        "python3", str((tmp_path / "echo_adapter.py").resolve()),
    )
