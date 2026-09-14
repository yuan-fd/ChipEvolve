"""The plugin conformance tool.

It reports; it does not admit.  These tests are as much about what it declines to
check as about what it catches: a validator that rejects what the platform
accepts is worse than no validator, because a plugin author would change a
correct plugin to satisfy it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ADMISSIONS = REPO_ROOT / "admissions"

#: The validator is a module of the registry package, so running it as a
#: subprocess needs the source roots on PYTHONPATH -- the same explicit wiring
#: pytest.ini does in-process.  No code mutates sys.path to achieve this.
SOURCE_ROOTS = [
    REPO_ROOT / "contracts" / "src",
    REPO_ROOT / "core" / "registry" / "src",
]


def manifest(**overrides) -> dict:
    payload = {
        "schema_version": 3,
        "plugin_id": "my-capability",
        "plugin_version": "1.0.0",
        "adapter_entry": ["python3", "./adapter.py"],
        "capabilities": ["my.capability"],
        "supported_arch": ["aarch64"],
    }
    payload.update(overrides)
    return payload


def make_plugin(root: Path, *, payload: dict | None = None,
                provenance: dict | None = None,
                adapter: bool = True) -> Path:
    directory = root / "my-capability"
    directory.mkdir(parents=True)
    (directory / "my-capability.plugin.json").write_text(
        json.dumps(payload if payload is not None else manifest()),
        encoding="utf-8")
    if adapter:
        (directory / "adapter.py").write_text("#!/usr/bin/env python3\n",
                                              encoding="utf-8")
    if provenance is not None:
        (directory / "provenance.json").write_text(json.dumps(provenance),
                                                   encoding="utf-8")
    return directory


def validate(*args: str) -> tuple[int, str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(p) for p in SOURCE_ROOTS]
        + ([environment["PYTHONPATH"]] if environment.get("PYTHONPATH") else [])
    )
    completed = subprocess.run(
        [sys.executable, "-m", "openroad_platform_registry.validate", *args],
        cwd=str(REPO_ROOT), env=environment,
        capture_output=True, text=True, timeout=60,
    )
    return completed.returncode, completed.stdout + completed.stderr


# --------------------------------------------------------------------------
# the real plugins in this repository
# --------------------------------------------------------------------------

def test_every_shipped_plugin_complies():
    """A directory with no manifest is not a plugin and is not checked.

    The protocol says the platform skips such a directory -- it may be a
    half-written checkout -- so a sweep of the plugin root must skip it too.  An
    earlier version of this test passed every directory to the validator and
    failed on a leftover empty one, which is the test being stricter than the
    platform.
    """
    plugins = sorted(p for p in (REPO_ROOT / "plugins").iterdir()
                     if p.is_dir() and list(p.glob("*.plugin.json")))
    assert plugins, "no plugins found to check"
    code, output = validate(*[str(p) for p in plugins],
                            "--admissions-root", str(ADMISSIONS))
    assert code == 0, output
    assert "comply" in output


# --------------------------------------------------------------------------
# what it refuses
# --------------------------------------------------------------------------

def test_a_plugin_with_no_manifest_is_refused(tmp_path):
    (tmp_path / "empty").mkdir()
    code, output = validate(str(tmp_path / "empty"))
    assert code == 1
    assert "no *.plugin.json" in output


def test_a_manifest_from_another_schema_version_is_refused(tmp_path):
    directory = make_plugin(tmp_path, payload=manifest(schema_version=2))
    code, output = validate(str(directory))
    assert code == 1
    assert "schema_version" in output


def test_a_missing_adapter_file_is_refused(tmp_path):
    directory = make_plugin(tmp_path, adapter=False)
    code, output = validate(str(directory))
    assert code == 1
    assert "names a file that is absent" in output


def test_an_entry_that_climbs_out_is_refused(tmp_path):
    directory = make_plugin(
        tmp_path, payload=manifest(adapter_entry=["python3", "../elsewhere.py"]))
    code, output = validate(str(directory))
    assert code == 1
    assert "climbs out" in output


def test_a_duplicated_artifact_kind_is_refused(tmp_path):
    directory = make_plugin(tmp_path, payload=manifest(artifact_rules=[
        {"kind": "report", "required": True},
        {"kind": "report", "required": False},
    ]))
    code, output = validate(str(directory))
    assert code == 1
    assert "more than once" in output


def test_a_provenance_file_with_an_unknown_field_is_refused(tmp_path):
    directory = make_plugin(tmp_path, provenance={"status": "admitted"})
    code, output = validate(str(directory))
    assert code == 1
    assert "not valid provenance" in output


def test_an_admission_record_for_another_plugin_is_refused(tmp_path):
    directory = make_plugin(tmp_path)
    admissions = tmp_path / "admissions"
    admissions.mkdir()
    (admissions / "my-capability.json").write_text(json.dumps({
        "plugin_id": "somebody-else", "status": "admitted",
        "license_review": "green", "approved_commit": "0" * 40,
        "reason": "for the test",
    }), encoding="utf-8")
    code, output = validate(str(directory), "--admissions-root", str(admissions))
    assert code == 1
    assert "not admissible" in output


# --------------------------------------------------------------------------
# what it deliberately does not refuse
# --------------------------------------------------------------------------

def test_an_absolute_entrypoint_is_reported_not_rejected(tmp_path):
    """It may exist only in the deployment environment.

    Requiring it here would fail every plugin whose deployment is not the
    developer's machine, which is most of them.
    """
    directory = make_plugin(tmp_path, payload=manifest(
        adapter_entry=["/opt/team-x/venv/bin/python", "./adapter.py"]))
    code, output = validate(str(directory))
    assert code == 0, output
    assert "not checked for existence" in output


def test_a_reserved_artifact_kind_may_be_listed(tmp_path):
    """The evaluator has to list one; listing grants nothing.

    This is the case that made the tool's first draft wrong: it refused what the
    platform accepts, and the platform is right.  The authority to declare a
    reserved kind is the platform's evaluation path, not the manifest's.
    """
    directory = make_plugin(tmp_path, payload=manifest(
        artifact_rules=[{"kind": "protected_evaluation", "required": True}]))
    code, output = validate(str(directory))
    assert code == 0, output
    assert "grants no authority" in output


def test_a_plugin_without_provenance_is_allowed_but_noted(tmp_path):
    directory = make_plugin(tmp_path)
    code, output = validate(str(directory))
    assert code == 0, output
    assert "states no origin" in output


def test_a_plugin_without_an_admission_record_is_allowed_but_noted(tmp_path):
    """Reporting the decision is not making it."""
    directory = make_plugin(tmp_path)
    admissions = tmp_path / "admissions"
    admissions.mkdir()
    code, output = validate(str(directory), "--admissions-root", str(admissions))
    assert code == 0, output
    assert "refuse to execute until a platform reviews it" in output


def test_without_an_admissions_root_it_says_so(tmp_path):
    directory = make_plugin(tmp_path)
    code, output = validate(str(directory))
    assert code == 0, output
    assert "not examined" in output
