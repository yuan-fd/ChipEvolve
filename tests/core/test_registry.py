"""Plugin discovery and admission.

These tests are about the platform's willingness to say no.  A registry that
resolves anything is not governing anything, and the previous platform proved
it: it recorded license conclusions that nothing ever read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openroad_platform_registry import (
    ADMITTED,
    INTAKE_FILENAME,
    PluginRegistry,
    RegistryError,
    SOURCE_AUDIT_ONLY,
    UNKNOWN,
)


def manifest_payload(plugin_id: str = "capability", **overrides) -> dict:
    payload = {
        "schema_version": 3,
        "plugin_id": plugin_id,
        "plugin_version": "1.0.0",
        "adapter_entry": ["python3", "./adapter.py"],
        "capabilities": ["do.thing"],
        "supported_arch": ["aarch64", "x86_64", "arm64"],
"artifact_rules": [{"kind": "report", "required": True}],
    }
    payload.update(overrides)
    return payload


def write_plugin(root: Path, name: str, *, manifest: dict | None = None,
                 intake: dict | None = None, adapter: bool = True) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / f"{name}.plugin.json").write_text(
        json.dumps(manifest if manifest is not None else manifest_payload(name)),
        encoding="utf-8",
    )
    if adapter:
        (directory / "adapter.py").write_text(
            "import argparse\n"
            "p = argparse.ArgumentParser()\n"
            "p.add_argument('--request'); p.add_argument('--result')\n"
            "p.parse_args()\n",
            encoding="utf-8",
        )
    if intake is not None:
        (directory / INTAKE_FILENAME).write_text(json.dumps(intake), encoding="utf-8")
    return directory


ADMITTED_INTAKE = {
    "status": "admitted",
    "license": "green",
    "source_url": "https://example.invalid/upstream",
    "source_commit": "0" * 40,
}


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

def test_discovery_finds_every_manifest_under_the_root(tmp_path):
    write_plugin(tmp_path, "alpha", intake=ADMITTED_INTAKE)
    write_plugin(tmp_path, "beta", manifest=manifest_payload("beta"),
                 intake=ADMITTED_INTAKE)
    registry = PluginRegistry.from_directory(tmp_path)
    assert [p.manifest.plugin_id for p in registry.list()] == ["alpha", "beta"]


def test_a_directory_without_a_manifest_is_ignored(tmp_path):
    write_plugin(tmp_path, "alpha", intake=ADMITTED_INTAKE)
    (tmp_path / "half-written-checkout").mkdir()
    (tmp_path / "half-written-checkout" / "README.md").write_text("wip")
    assert len(PluginRegistry.from_directory(tmp_path).list()) == 1


def test_a_missing_plugin_root_is_an_error_not_an_empty_registry(tmp_path):
    with pytest.raises(RegistryError, match="plugin root not found"):
        PluginRegistry.from_directory(tmp_path / "absent")


def test_registering_the_same_identity_twice_is_refused(tmp_path):
    directory = write_plugin(tmp_path, "alpha", intake=ADMITTED_INTAKE)
    registry = PluginRegistry.from_directory(tmp_path)
    duplicate = PluginRegistry.from_directory(tmp_path)
    with pytest.raises(RegistryError, match="already registered"):
        registry.register(duplicate.list()[0])


# --------------------------------------------------------------------------
# admission -- the part v1 never enforced
# --------------------------------------------------------------------------

def test_a_plugin_without_intake_is_discoverable_but_not_executable(tmp_path):
    write_plugin(tmp_path, "unlicensed")
    registry = PluginRegistry.from_directory(tmp_path)

    plugin = registry.get("unlicensed")
    assert plugin.admission.status is UNKNOWN
    assert plugin.executable is False
    assert "intake" in plugin.admission.reason

    # Visible in the catalogue...
    assert registry.catalogue()[0]["executable"] is False
    # ...and refuses to run, by name and with a reason.
    with pytest.raises(RegistryError, match="not admitted"):
        registry.resolve("unlicensed")


def test_source_audit_only_plugin_refuses_to_execute(tmp_path):
    write_plugin(tmp_path, "red", intake={
        "status": SOURCE_AUDIT_ONLY,
        "license": "red",
        "reason": "upstream publishes no license file",
    })
    registry = PluginRegistry.from_directory(tmp_path)
    with pytest.raises(RegistryError, match="not admitted"):
        registry.resolve("red")
    # ...but it can still be inspected without executing it.
    assert registry.resolve("red", require_admitted=False).plugin_id == "red"


def test_an_admitted_plugin_resolves(tmp_path):
    write_plugin(tmp_path, "alpha", intake=ADMITTED_INTAKE)
    registry = PluginRegistry.from_directory(tmp_path)
    assert registry.resolve("alpha").plugin_id == "alpha"
    assert registry.resolve("alpha", capability="do.thing").plugin_id == "alpha"


def test_admitted_without_a_green_license_is_contradictory(tmp_path):
    write_plugin(tmp_path, "liar", intake={
        "status": ADMITTED, "license": "red", "source_commit": "0" * 40,
    })
    with pytest.raises(RegistryError, match="license conclusion"):
        PluginRegistry.from_directory(tmp_path)


def test_admitted_without_a_pinned_commit_is_refused(tmp_path):
    write_plugin(tmp_path, "floating", intake={
        "status": ADMITTED, "license": "green",
        "source_url": "https://example.invalid/x",
    })
    with pytest.raises(RegistryError, match="pinned commit"):
        PluginRegistry.from_directory(tmp_path)


def test_a_blocked_plugin_must_record_why(tmp_path):
    write_plugin(tmp_path, "mute", intake={"status": SOURCE_AUDIT_ONLY})
    with pytest.raises(RegistryError, match="no reason recorded"):
        PluginRegistry.from_directory(tmp_path)


def test_unreadable_intake_blocks_execution_rather_than_passing(tmp_path):
    directory = write_plugin(tmp_path, "broken")
    (directory / INTAKE_FILENAME).write_text("{not json", encoding="utf-8")
    registry = PluginRegistry.from_directory(tmp_path)
    assert registry.get("broken").executable is False


# --------------------------------------------------------------------------
# adapter entry containment -- the v1 "plugin points back into the platform"
# --------------------------------------------------------------------------

def test_a_relative_entry_resolves_against_the_plugin_directory(tmp_path):
    write_plugin(tmp_path, "alpha", intake=ADMITTED_INTAKE)
    registry = PluginRegistry.from_directory(tmp_path)
    entry = registry.get("alpha").adapter_entry
    assert entry[0] == "python3"
    assert Path(entry[1]).name == "adapter.py"
    assert Path(entry[1]).parent == (tmp_path / "alpha").resolve()


def test_an_entry_that_climbs_out_of_its_directory_is_refused(tmp_path):
    write_plugin(
        tmp_path, "parasite",
        manifest=manifest_payload("parasite",
                                  adapter_entry=["python3", "../elsewhere.py"]),
        intake=ADMITTED_INTAKE,
    )
    with pytest.raises(RegistryError, match="escapes|climbs out"):
        PluginRegistry.from_directory(tmp_path)


def test_an_entry_pointing_at_the_platform_package_is_refused(tmp_path):
    """The exact v1 defect: an adapter_entry like ./../../packages/.../x.py."""
    write_plugin(
        tmp_path, "parasite",
        manifest=manifest_payload(
            "parasite",
            adapter_entry=["python3", "./../../packages/core/adapter.py"],
        ),
        intake=ADMITTED_INTAKE,
    )
    with pytest.raises(RegistryError, match="escapes"):
        PluginRegistry.from_directory(tmp_path)


# --------------------------------------------------------------------------
# lookup
# --------------------------------------------------------------------------

def test_a_capability_the_plugin_does_not_declare_is_refused(tmp_path):
    write_plugin(tmp_path, "alpha", intake=ADMITTED_INTAKE)
    registry = PluginRegistry.from_directory(tmp_path)
    with pytest.raises(RegistryError, match="lacks capability"):
        registry.resolve("alpha", capability="do.something.else")


def test_an_unsupported_architecture_is_refused(tmp_path):
    write_plugin(tmp_path, "alpha", intake=ADMITTED_INTAKE)
    registry = PluginRegistry.from_directory(tmp_path)
    with pytest.raises(RegistryError, match="does not support architecture"):
        registry.resolve("alpha", arch="riscv64")


def test_an_unknown_plugin_is_refused(tmp_path):
    write_plugin(tmp_path, "alpha", intake=ADMITTED_INTAKE)
    registry = PluginRegistry.from_directory(tmp_path)
    with pytest.raises(RegistryError, match="unknown plugin"):
        registry.resolve("absent")


def test_an_ambiguous_version_must_be_disambiguated(tmp_path):
    write_plugin(tmp_path, "alpha", intake=ADMITTED_INTAKE)
    write_plugin(
        tmp_path, "alpha_v2",
        manifest=manifest_payload("alpha", plugin_version="2.0.0"),
        intake=ADMITTED_INTAKE,
    )
    registry = PluginRegistry.from_directory(tmp_path)
    with pytest.raises(RegistryError, match="several versions"):
        registry.resolve("alpha")
    assert registry.resolve("alpha", version="2.0.0").plugin_version == "2.0.0"


def test_the_catalogue_reports_state_without_executing_anything(tmp_path):
    write_plugin(tmp_path, "alpha", intake=ADMITTED_INTAKE)
    write_plugin(tmp_path, "blocked", intake={
        "status": SOURCE_AUDIT_ONLY, "license": "red", "reason": "no license",
    })
    catalogue = {row["plugin_id"]: row
                 for row in PluginRegistry.from_directory(tmp_path).catalogue()}
    assert catalogue["alpha"]["executable"] is True
    assert catalogue["blocked"]["executable"] is False
    assert catalogue["blocked"]["reason"] == "no license"
