"""Plugin discovery and admission.

These tests are about the platform's willingness to say no.  A registry that
resolves anything is not governing anything, and the previous platform proved
it: it recorded licence conclusions that nothing ever read.

Two files, two owners.  A plugin declares its own origin, in its own directory,
and that declaration grants nothing.  Whether the platform executes it is the
platform's record, in its own directory, and this file tests the separation as
much as it tests the refusals.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openroad_platform_registry import (
    ADMITTED,
    ADMISSION_SUFFIX,
    PROVENANCE_FILENAME,
    PluginRegistry,
    RegistryError,
    SOURCE_AUDIT_ONLY,
    UNKNOWN,
)

PLUGINS = "plugins"
ADMISSIONS = "admissions"
#: The revision the test reviewer looked at.
REVIEWED_COMMIT = "0" * 40


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
                 provenance: dict | None = None, adapter: bool = True) -> Path:
    """The plugin's own directory: its declaration, its origin, its code."""
    directory = root / PLUGINS / name
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
    if provenance is not None:
        (directory / PROVENANCE_FILENAME).write_text(
            json.dumps(provenance), encoding="utf-8")
    return directory


def admit(root: Path, plugin_id: str, **overrides) -> Path:
    """The platform's own trust record.  Not the plugin's file."""
    record = {
        "plugin_id": plugin_id,
        "status": ADMITTED,
        "license_review": "green",
        "approved_commit": REVIEWED_COMMIT,
        "reviewer": "test-reviewer",
        "reason": "reviewed for this test",
    }
    record.update(overrides)
    path = root / ADMISSIONS / f"{plugin_id}{ADMISSION_SUFFIX}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def discover(root: Path, *, admissions: bool = True) -> PluginRegistry:
    return PluginRegistry.from_directory(
        root / PLUGINS,
        admissions_root=(root / ADMISSIONS) if admissions else None,
    )


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

def test_discovery_finds_every_manifest_under_the_root(tmp_path):
    write_plugin(tmp_path, "alpha")
    admit(tmp_path, "alpha")
    write_plugin(tmp_path, "beta", manifest=manifest_payload("beta"))
    admit(tmp_path, "beta")
    registry = discover(tmp_path)
    assert [p.manifest.plugin_id for p in registry.list()] == ["alpha", "beta"]


def test_a_directory_without_a_manifest_is_ignored(tmp_path):
    write_plugin(tmp_path, "alpha")
    admit(tmp_path, "alpha")
    half = tmp_path / PLUGINS / "half-written-checkout"
    half.mkdir()
    (half / "README.md").write_text("wip")
    assert len(discover(tmp_path).list()) == 1


def test_a_missing_plugin_root_is_an_error_not_an_empty_registry(tmp_path):
    with pytest.raises(RegistryError, match="plugin root not found"):
        PluginRegistry.from_directory(tmp_path / "absent")


def test_registering_the_same_identity_twice_is_refused(tmp_path):
    write_plugin(tmp_path, "alpha")
    admit(tmp_path, "alpha")
    registry = discover(tmp_path)
    duplicate = discover(tmp_path)
    with pytest.raises(RegistryError, match="already registered"):
        registry.register(duplicate.list()[0])


# --------------------------------------------------------------------------
# the two files have two owners
# --------------------------------------------------------------------------

def test_a_plugin_without_an_admission_record_is_listed_but_not_executable(
    tmp_path,
):
    """Unreviewed is a state, not a crash and not a pass."""
    write_plugin(tmp_path, "unreviewed")

    plugin = discover(tmp_path).get("unreviewed")
    assert plugin.admission.status is UNKNOWN
    assert plugin.executable is False
    assert "no admission record" in plugin.admission.reason


def test_a_plugin_cannot_admit_itself(tmp_path):
    """The decision does not live in the plugin's directory.

    A plugin that writes a trust decision into its own provenance file is told
    so, loudly, rather than having the field ignored: silently dropping it would
    leave the author believing they had been admitted.
    """
    write_plugin(tmp_path, "self-promoter", provenance={
        "status": "admitted", "license_review": "green",
    })
    with pytest.raises(RegistryError, match="unknown provenance fields.*status"):
        discover(tmp_path)


def test_without_an_admissions_directory_nothing_executes(tmp_path):
    """The platform's records are not optional; there is no implicit trust."""
    write_plugin(tmp_path, "alpha")
    registry = discover(tmp_path, admissions=False)
    assert registry.get("alpha").executable is False
    with pytest.raises(RegistryError, match="no admissions directory"):
        registry.resolve("alpha")


def test_the_plugin_declares_its_own_origin(tmp_path):
    """What the plugin says about itself is kept, and kept separate."""
    write_plugin(tmp_path, "alpha", provenance={
        "license": "Apache-2.0",
        "source_url": "https://example.invalid/alpha",
        "source_commit": REVIEWED_COMMIT,
        "notes": "vendors nothing",
    })
    admit(tmp_path, "alpha")
    plugin = discover(tmp_path).get("alpha")
    assert plugin.provenance.license == "Apache-2.0"
    assert plugin.provenance.source_url == "https://example.invalid/alpha"
    # And the platform's own conclusion is a different field, not the same one.
    assert plugin.admission.license_review == "green"


# --------------------------------------------------------------------------
# admission -- the part v1 never enforced
# --------------------------------------------------------------------------

def test_source_audit_only_plugin_refuses_to_execute(tmp_path):
    write_plugin(tmp_path, "red")
    admit(tmp_path, "red", status=SOURCE_AUDIT_ONLY, license_review="red",
          reason="upstream publishes no license file")
    registry = discover(tmp_path)
    with pytest.raises(RegistryError, match="not admitted"):
        registry.resolve("red")
    # ...but it can still be inspected without executing it.
    assert registry.resolve("red", require_admitted=False).plugin_id == "red"


def test_an_admitted_plugin_resolves(tmp_path):
    write_plugin(tmp_path, "alpha")
    admit(tmp_path, "alpha")
    registry = discover(tmp_path)
    assert registry.resolve("alpha").plugin_id == "alpha"
    assert registry.resolve("alpha", capability="do.thing").plugin_id == "alpha"


def test_admitted_without_a_green_review_is_contradictory(tmp_path):
    write_plugin(tmp_path, "liar")
    admit(tmp_path, "liar", license_review="red")
    with pytest.raises(RegistryError, match="licence review"):
        discover(tmp_path)


def test_admitted_without_a_pinned_commit_is_refused(tmp_path):
    write_plugin(tmp_path, "floating")
    admit(tmp_path, "floating", approved_commit=None)
    with pytest.raises(RegistryError, match="pinned commit"):
        discover(tmp_path)


def test_a_blocked_plugin_must_record_why(tmp_path):
    write_plugin(tmp_path, "mute")
    admit(tmp_path, "mute", status=SOURCE_AUDIT_ONLY, reason=None)
    with pytest.raises(RegistryError, match="no reason recorded"):
        discover(tmp_path)


def test_an_unreadable_admission_record_blocks_execution(tmp_path):
    write_plugin(tmp_path, "broken")
    path = admit(tmp_path, "broken")
    path.write_text("{not json", encoding="utf-8")
    registry = discover(tmp_path)
    assert registry.get("broken").executable is False


def test_a_record_for_another_plugin_does_not_admit_this_one(tmp_path):
    """An admission file names the plugin it is about.

    Keyed by plugin id, and checked: a record copied to the wrong filename would
    otherwise admit whatever happened to be there.
    """
    write_plugin(tmp_path, "alpha")
    path = admit(tmp_path, "alpha")
    record = json.loads(path.read_text(encoding="utf-8"))
    record["plugin_id"] = "beta"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(RegistryError, match="declares 'beta'"):
        discover(tmp_path)


# --------------------------------------------------------------------------
# the reviewed revision is the revision that runs
# --------------------------------------------------------------------------

def test_a_checkout_that_is_not_the_reviewed_commit_is_refused(tmp_path):
    """Otherwise "pinned commit" is a word rather than a property.

    A team that moves their source needs a new review; the platform will not
    quietly execute a revision nobody looked at.
    """
    write_plugin(tmp_path, "moved", provenance={
        "source_commit": "1" * 40,
    })
    admit(tmp_path, "moved", approved_commit="2" * 40)
    with pytest.raises(RegistryError, match="not the revision that was reviewed"):
        discover(tmp_path)


def test_a_matching_commit_is_admitted(tmp_path):
    write_plugin(tmp_path, "same", provenance={"source_commit": REVIEWED_COMMIT})
    admit(tmp_path, "same", approved_commit=REVIEWED_COMMIT)
    assert discover(tmp_path).get("same").executable is True


def test_a_plugin_that_states_no_commit_is_not_blocked_by_the_pin(tmp_path):
    """Silence is not a mismatch.  The record only constrains a stated revision."""
    write_plugin(tmp_path, "quiet", provenance={"license": "MIT"})
    admit(tmp_path, "quiet", approved_commit=REVIEWED_COMMIT)
    assert discover(tmp_path).get("quiet").executable is True


# --------------------------------------------------------------------------
# adapter entry containment -- the v1 "plugin points back into the platform"
# --------------------------------------------------------------------------

def test_a_relative_entry_resolves_against_the_plugin_directory(tmp_path):
    write_plugin(tmp_path, "alpha")
    admit(tmp_path, "alpha")
    entry = discover(tmp_path).get("alpha").adapter_entry
    assert entry[0] == "python3"
    assert Path(entry[1]).name == "adapter.py"
    assert Path(entry[1]).parent == (tmp_path / PLUGINS / "alpha").resolve()


def test_an_entry_that_climbs_out_of_its_directory_is_refused(tmp_path):
    write_plugin(
        tmp_path, "parasite",
        manifest=manifest_payload("parasite",
                                  adapter_entry=["python3", "../elsewhere.py"]),
    )
    with pytest.raises(RegistryError, match="escapes|climbs out"):
        discover(tmp_path)


def test_an_entry_pointing_at_the_platform_package_is_refused(tmp_path):
    """The exact v1 defect: an adapter_entry like ./../../packages/.../x.py."""
    write_plugin(
        tmp_path, "parasite",
        manifest=manifest_payload(
            "parasite",
            adapter_entry=["python3", "./../../packages/core/adapter.py"],
        ),
    )
    with pytest.raises(RegistryError, match="escapes"):
        discover(tmp_path)


def test_an_absolute_entry_is_left_alone(tmp_path):
    """A plugin may name its own interpreter, wherever it is installed.

    That is what lets a team keep its own environment: the entry is an argv
    prefix, not necessarily this platform's Python.
    """
    write_plugin(
        tmp_path, "external",
        manifest=manifest_payload(
            "external", adapter_entry=["/opt/team-x/venv/bin/python", "./adapter.py"]),
    )
    admit(tmp_path, "external")
    entry = discover(tmp_path).get("external").adapter_entry
    assert entry[0] == "/opt/team-x/venv/bin/python"


# --------------------------------------------------------------------------
# lookup
# --------------------------------------------------------------------------

def test_a_capability_the_plugin_does_not_declare_is_refused(tmp_path):
    write_plugin(tmp_path, "alpha")
    admit(tmp_path, "alpha")
    with pytest.raises(RegistryError, match="lacks capability"):
        discover(tmp_path).resolve("alpha", capability="do.something.else")


def test_an_unsupported_architecture_is_refused(tmp_path):
    write_plugin(tmp_path, "alpha")
    admit(tmp_path, "alpha")
    with pytest.raises(RegistryError, match="does not support architecture"):
        discover(tmp_path).resolve("alpha", arch="riscv64")


def test_an_unknown_plugin_is_refused(tmp_path):
    write_plugin(tmp_path, "alpha")
    admit(tmp_path, "alpha")
    with pytest.raises(RegistryError, match="unknown plugin"):
        discover(tmp_path).resolve("absent")


def test_an_ambiguous_version_must_be_disambiguated(tmp_path):
    write_plugin(tmp_path, "alpha")
    admit(tmp_path, "alpha")
    write_plugin(
        tmp_path, "alpha_v2",
        manifest=manifest_payload("alpha", plugin_version="2.0.0"),
    )
    registry = discover(tmp_path)
    with pytest.raises(RegistryError, match="several versions"):
        registry.resolve("alpha")
    assert registry.resolve("alpha", version="2.0.0").plugin_version == "2.0.0"


def test_the_catalogue_reports_both_sources_without_executing_anything(tmp_path):
    """A reader must be able to tell which party said what."""
    write_plugin(tmp_path, "alpha", provenance={"license": "MIT"})
    admit(tmp_path, "alpha")
    write_plugin(tmp_path, "blocked")
    admit(tmp_path, "blocked", status=SOURCE_AUDIT_ONLY, license_review="red",
          reason="no license")

    catalogue = {row["plugin_id"]: row for row in discover(tmp_path).catalogue()}
    assert catalogue["alpha"]["executable"] is True
    assert catalogue["alpha"]["declared"]["license"] == "MIT"
    assert catalogue["alpha"]["license_review"] == "green"
    assert catalogue["alpha"]["reviewer"] == "test-reviewer"
    assert catalogue["blocked"]["executable"] is False
    assert catalogue["blocked"]["reason"] == "no license"
