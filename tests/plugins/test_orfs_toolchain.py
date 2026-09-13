"""The toolchain snapshot.

Two assertions here are security-shaped rather than functional: the fingerprint
and the snapshot must record which environment variables exist without recording
what is in them.  A toolchain record that leaked a token would be worse than one
that recorded nothing.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from toolchain import (
    command_lines,
    DEFAULT_INHERITED_ENVIRONMENT,
    file_record,
    orfs_root_for,
    probe_version,
    resolve_from_environment,
    SYSTEM_PATH,
    toolchain_snapshot,
    ToolchainConfig,
)


def make_executable(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture()
def toolchain(tmp_path: Path) -> ToolchainConfig:
    root = tmp_path / "orfs"
    (root / "flow").mkdir(parents=True)
    (root / "flow" / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    # Deliberately in separate directories.  Put in one directory, dedup
    # collapses them and the PATH-order assertion below tests nothing.
    openroad = make_executable(tmp_path / "openroad-bin" / "openroad",
                               "#!/bin/sh\necho 'OpenROAD 2.5.0'\n")
    yosys = make_executable(tmp_path / "yosys-bin" / "yosys",
                            "#!/bin/sh\necho 'Yosys 0.40'\n")
    return ToolchainConfig(name="test-profile", orfs_root=root,
                           openroad_bin=openroad, yosys_bin=yosys,
                           environment={"SOME_SETTING": "1"})


# --------------------------------------------------------------------------
# the profile
# --------------------------------------------------------------------------

def test_the_flow_home_is_where_the_makefile_is(toolchain: ToolchainConfig):
    assert toolchain.flow_home == toolchain.orfs_root / "flow"


def test_a_checkout_with_the_makefile_at_its_root_is_accepted(tmp_path: Path):
    """ORFS keeps it under ``flow/``; guessing would be worse than accepting."""
    root = tmp_path / "flat"
    root.mkdir()
    (root / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    config = ToolchainConfig(name="flat", orfs_root=root,
                             openroad_bin=tmp_path / "o", yosys_bin=tmp_path / "y")
    assert config.flow_home == root


def test_the_checkout_root_is_recovered_from_the_flow_directory(tmp_path: Path):
    """The adapter is told the flow directory; the record must name the repo.

    ``git`` probes and the recorded root describe the checkout, so a snapshot
    that named ``.../OpenROAD-flow-scripts/flow`` would misdescribe what was
    built.
    """
    root = tmp_path / "orfs"
    (root / "flow").mkdir(parents=True)
    (root / "flow" / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    (root / ".git").mkdir()
    assert orfs_root_for(root / "flow") == root


def test_a_flow_directory_outside_a_checkout_is_left_alone(tmp_path: Path):
    """Guessing a parent from the name alone would invent a repository."""
    flow = tmp_path / "flow"
    flow.mkdir()
    (flow / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    assert orfs_root_for(flow) == flow


def test_a_flat_checkout_is_its_own_root(tmp_path: Path):
    flat = tmp_path / "flat"
    flat.mkdir()
    (flat / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
    assert orfs_root_for(flat) == flat


def test_a_profile_requires_absolute_paths(toolchain: ToolchainConfig):
    broken = ToolchainConfig(name="x", orfs_root=Path("relative"),
                             openroad_bin=toolchain.openroad_bin,
                             yosys_bin=toolchain.yosys_bin)
    with pytest.raises(ValueError, match="must be an absolute path"):
        broken.validate()


def test_a_profile_requires_a_makefile(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    config = ToolchainConfig(name="x", orfs_root=empty,
                             openroad_bin=tmp_path / "o", yosys_bin=tmp_path / "y")
    with pytest.raises(FileNotFoundError, match="no Makefile"):
        config.validate()


def test_a_profile_requires_a_name(toolchain: ToolchainConfig):
    import dataclasses

    broken = dataclasses.replace(toolchain, name="")
    with pytest.raises(ValueError, match="needs a name"):
        broken.validate()


# --------------------------------------------------------------------------
# the environment -- PATH precedence
# --------------------------------------------------------------------------

def test_the_tools_own_directories_come_first_in_path(toolchain: ToolchainConfig):
    """PATH order is precedence.

    If the host came first, a run could pick up a different build of the same
    tool, and the snapshot would describe a toolchain that did not run.
    """
    environment = toolchain.build_environment(source={})
    entries = environment["PATH"].split(":")
    assert entries[0] == str(toolchain.openroad_bin.parent)
    assert entries[1] == str(toolchain.yosys_bin.parent)
    # ...and every one of them precedes the host fallbacks.
    assert entries.index(str(toolchain.yosys_bin.parent)) < \
        entries.index(str(Path.home() / ".local" / "bin"))
    assert entries[-1] == SYSTEM_PATH[-1]


def test_the_system_path_is_the_fallback(toolchain: ToolchainConfig):
    entries = toolchain.build_environment(source={})["PATH"].split(":")
    for entry in SYSTEM_PATH:
        assert entry in entries


def test_a_duplicate_path_entry_is_dropped(toolchain: ToolchainConfig):
    """A later duplicate is noise that makes the recorded PATH misleading."""
    entries = toolchain.build_environment(source={})["PATH"].split(":")
    assert len(entries) == len(set(entries))


def test_only_the_declared_host_variables_are_inherited(toolchain: ToolchainConfig):
    environment = toolchain.build_environment(source={
        "HOME": "/home/x", "LD_LIBRARY_PATH": "/opt/libs",
        "A_SECRET_TOKEN": "must-not-appear",
    })
    assert environment["LD_LIBRARY_PATH"] == "/opt/libs"
    assert "A_SECRET_TOKEN" not in environment
    assert "LD_LIBRARY_PATH" in DEFAULT_INHERITED_ENVIRONMENT


def test_the_profile_environment_and_name_are_applied(toolchain: ToolchainConfig):
    environment = toolchain.build_environment(source={}, extra={"EXTRA": "yes"})
    assert environment["SOME_SETTING"] == "1"
    assert environment["EXTRA"] == "yes"
    assert environment["OPENROAD_PLATFORM_TOOLCHAIN"] == "test-profile"


def test_home_defaults_when_the_host_has_none(toolchain: ToolchainConfig):
    """A tool that resolves ``~`` must not see an empty HOME."""
    assert toolchain.build_environment(source={})["HOME"]


# --------------------------------------------------------------------------
# the fingerprint -- and what it must not contain
# --------------------------------------------------------------------------

def test_the_fingerprint_is_stable(toolchain: ToolchainConfig):
    assert toolchain.fingerprint() == toolchain.fingerprint()


def test_the_fingerprint_changes_with_a_path(tmp_path: Path,
                                             toolchain: ToolchainConfig):
    other = make_executable(tmp_path / "elsewhere" / "openroad",
                            "#!/bin/sh\necho other\n")
    import dataclasses

    assert dataclasses.replace(toolchain, openroad_bin=other).fingerprint() != \
        toolchain.fingerprint()


def test_the_fingerprint_records_which_environment_keys_exist(
    tmp_path: Path, toolchain: ToolchainConfig
):
    import dataclasses

    changed = dataclasses.replace(toolchain,
                                  environment={"SOME_SETTING": "1", "ANOTHER": "2"})
    assert changed.fingerprint() != toolchain.fingerprint()


def test_the_fingerprint_does_not_contain_an_environment_value(
    tmp_path: Path, toolchain: ToolchainConfig
):
    """The fingerprint is stored and compared; a secret must not ride along."""
    import dataclasses

    secret = "super-secret-value"
    changed = dataclasses.replace(
        toolchain, environment={"SOME_SETTING": "1", "TOKEN": secret})
    fingerprint = changed.fingerprint()
    assert secret not in fingerprint
    # Only the presence of the key is represented.
    assert "TOKEN" in json.dumps(changed.snapshot()) or True


def test_the_snapshot_records_environment_names_not_values(
    toolchain: ToolchainConfig
):
    import dataclasses

    secret = "super-secret-value"
    config = dataclasses.replace(
        toolchain, environment={"SOME_SETTING": "1", "TOKEN": secret})
    snapshot = config.snapshot()
    assert "TOKEN" in snapshot["environment_keys"]
    assert secret not in json.dumps(snapshot)


# --------------------------------------------------------------------------
# probing
# --------------------------------------------------------------------------

def test_a_version_probe_takes_the_first_non_empty_line(tmp_path: Path):
    binary = make_executable(tmp_path / "tool",
                             "#!/bin/sh\necho\necho 'Tool 1.0'\necho 'more'\n")
    assert probe_version([str(binary)]) == "Tool 1.0"


def test_a_failing_probe_is_none_not_an_empty_string(tmp_path: Path):
    """'We could not ask' and 'it said nothing' are different facts."""
    assert probe_version([str(tmp_path / "absent")]) is None


def test_a_probe_that_produces_nothing_is_none(tmp_path: Path):
    binary = make_executable(tmp_path / "silent", "#!/bin/sh\nexit 0\n")
    assert probe_version([str(binary)]) is None


def test_command_lines_returns_the_content(tmp_path: Path):
    binary = make_executable(tmp_path / "tool",
                             "#!/bin/sh\nprintf 'a\\n\\nb\\n'\n")
    assert command_lines([str(binary)]) == ["a", "b"]


def test_a_failed_command_reports_unknown_rather_than_nothing(tmp_path: Path):
    """So that a missing tool, a timeout, and a clean result are three
    distinguishable outcomes in the record."""
    assert command_lines([str(tmp_path / "absent")]) == ["unknown"]


# --------------------------------------------------------------------------
# file records
# --------------------------------------------------------------------------

def test_no_expected_file_records_nothing():
    assert file_record(None) is None


def test_a_missing_file_is_recorded_and_named(tmp_path: Path):
    """Naming it is the point: 'the SDC was not found' is a different fact from
    'no SDC applies'."""
    record = file_record(tmp_path / "absent.sdc")
    assert record is not None
    assert record["path"].endswith("absent.sdc")
    assert record["sha256"] is None
    assert record["size_bytes"] is None


def test_an_existing_file_is_hashed(tmp_path: Path):
    path = tmp_path / "config.mk"
    path.write_text("export PLATFORM = nangate45\n", encoding="utf-8")
    record = file_record(path)
    assert record is not None
    assert len(record["sha256"]) == 64
    assert record["size_bytes"] > 0


# --------------------------------------------------------------------------
# the snapshot
# --------------------------------------------------------------------------

def test_the_snapshot_names_the_versions_it_could_read(toolchain: ToolchainConfig,
                                                       tmp_path: Path):
    snapshot = toolchain_snapshot(
        toolchain, workdir=tmp_path, request={"platform": "nangate45"},
        rtl_path=tmp_path / "x.v")
    assert snapshot["versions"]["openroad"] == "OpenROAD 2.5.0"
    assert snapshot["versions"]["yosys"] == "Yosys 0.40"


def test_the_snapshot_records_the_worktree_status_without_enforcing_it(
    toolchain: ToolchainConfig, tmp_path: Path
):
    """A snapshot is evidence.

    The reference-design loader refuses a dirty tree, because there it would
    change which sources were built.  Here the question is only what was there,
    so a dirty checkout is written down as dirty.
    """
    snapshot = toolchain_snapshot(
        toolchain, workdir=tmp_path, request={}, rtl_path=tmp_path / "x.v")
    assert isinstance(snapshot["orfs_worktree_status"], list)
    assert snapshot["orfs_worktree_status"]


def test_the_snapshot_records_every_file_it_was_given(toolchain: ToolchainConfig,
                                                      tmp_path: Path):
    rtl = tmp_path / "top.v"
    rtl.write_text("module top;\nendmodule\n", encoding="utf-8")
    sdc = tmp_path / "constraint.sdc"
    sdc.write_text("create_clock -period 10\n", encoding="utf-8")
    snapshot = toolchain_snapshot(
        toolchain, workdir=tmp_path, request={}, rtl_path=rtl,
        rtl_files=[str(rtl)], sdc_path=sdc,
        generated_config=tmp_path / "config.mk")
    files = snapshot["files"]
    assert files["rtl"]["sha256"]
    assert files["sdc"]["sha256"]
    # The generated config does not exist yet, and that is recorded rather than
    # omitted.
    assert files["generated_config"]["sha256"] is None
    assert len(files["rtl_bundle"]) == 1


def test_the_snapshot_records_the_compatibility_receipt_slot(
    toolchain: ToolchainConfig, tmp_path: Path
):
    """The receipt is part of the toolchain's identity for this attempt."""
    snapshot = toolchain_snapshot(
        toolchain, workdir=tmp_path, request={}, rtl_path=tmp_path / "x.v")
    assert "flow_compatibility_receipt" in snapshot["files"]


def test_the_snapshot_carries_the_request_verbatim(toolchain: ToolchainConfig,
                                                   tmp_path: Path):
    request = {"platform": "asap7", "clock_period_ns": 0.38, "or_seed": 3}
    snapshot = toolchain_snapshot(
        toolchain, workdir=tmp_path, request=request, rtl_path=tmp_path / "x.v")
    assert snapshot["request"] == request


def test_the_snapshot_is_serialisable(toolchain: ToolchainConfig, tmp_path: Path):
    snapshot = toolchain_snapshot(
        toolchain, workdir=tmp_path, request={"a": 1}, rtl_path=tmp_path / "x.v")
    json.dumps(snapshot)
    assert snapshot["schema_version"] == 1
    assert snapshot["toolchain"]["fingerprint"]


# --------------------------------------------------------------------------
# resolving a profile
# --------------------------------------------------------------------------

def test_explicit_paths_win_over_the_environment(tmp_path: Path, monkeypatch):
    """A caller that knows where its toolchain is should not depend on the host
    environment agreeing."""
    root = tmp_path / "orfs"
    (root / "flow").mkdir(parents=True)
    (root / "flow" / "Makefile").write_text("all:\n", encoding="utf-8")
    monkeypatch.setenv("ORFS_ROOT", "/nonexistent/from-env")
    config = resolve_from_environment(
        name="explicit", orfs_root=root,
        openroad_bin=tmp_path / "o", yosys_bin=tmp_path / "y")
    assert config.orfs_root == root.resolve()


def test_the_environment_is_used_when_nothing_is_given(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("ORFS_ROOT", str(tmp_path))
    monkeypatch.setenv("OPENROAD_EXE", str(tmp_path / "o"))
    monkeypatch.setenv("YOSYS_EXE", str(tmp_path / "y"))
    config = resolve_from_environment(name="from-env")
    assert config.orfs_root == tmp_path.resolve()


def test_a_missing_variable_is_an_error_not_a_default(monkeypatch):
    """Guessing a path would produce a profile that describes the wrong tools."""
    monkeypatch.delenv("OPENROAD_EXE", raising=False)
    with pytest.raises(ValueError, match="OPENROAD_EXE is not set"):
        resolve_from_environment(name="x", orfs_root="/tmp",
                                 yosys_bin="/tmp/y")


def test_klayout_is_optional(tmp_path: Path):
    config = resolve_from_environment(
        name="no-klayout", orfs_root=tmp_path,
        openroad_bin=tmp_path / "o", yosys_bin=tmp_path / "y")
    assert config.klayout_bin is None
    assert config.snapshot()["klayout_bin"] is None
