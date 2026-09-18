"""Protocol smoke tests for the user-directory server Toolkit packages."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOLKITS_ROOT = Path.home() / "toolkits"
TOOLKITS = {
    "synopsys-icc2": "synopsys-icc2.plugin.json",
    "synopsys-primetime": "synopsys-primetime.plugin.json",
    "cadence-genus": "cadence-genus.plugin.json",
}


@pytest.mark.parametrize("toolkit,manifest_name", TOOLKITS.items())
def test_server_toolkit_protocol_smoke(
    tmp_path: Path, toolkit: str, manifest_name: str
):
    directory = TOOLKITS_ROOT / toolkit
    adapter = directory / "adapter.py"
    if not adapter.is_file():
        pytest.skip(f"server Toolkit is not installed: {directory}")
    request = tmp_path / "request.json"
    result = tmp_path / "result.json"
    request.write_text(
        json.dumps(
            {
                "protocol_version": 1,
                "plugin": {"plugin_id": toolkit, "plugin_version": "1.0.0"},
                "task": {
                    "inputs": {
                        "capability": "preflight",
                        "module": None,
                        "tool_path": "/bin/echo",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(adapter),
            "--request",
            str(request),
            "--result",
            str(result),
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(result.read_text(encoding="utf-8"))
    assert payload["status"] == "succeeded"
    assert {item["kind"] for item in payload["artifacts"]} == {"toolchain", "tool_log"}
    assert "license" not in payload["provenance"]


def test_orfs_toolkit_preflight_protocol_smoke(tmp_path: Path):
    directory = TOOLKITS_ROOT / "chipevolve-orfs"
    adapter = directory / "adapter.py"
    orfs_root = Path("/opt/EDAs/OpenROAD-flow-scripts/versions/20260409")
    if not adapter.is_file() or not orfs_root.is_dir():
        pytest.skip("server ORFS Toolkit or ORFS tree is not installed")
    request = tmp_path / "request.json"
    result = tmp_path / "result.json"
    request.write_text(
        json.dumps(
            {
                "protocol_version": 1,
                "plugin": {"plugin_id": "orfs", "plugin_version": "2.0.0"},
                "task": {
                    "inputs": {
                        "capability": "preflight",
                        "orfs_root": str(orfs_root),
                        "openroad_bin": "/bin/echo",
                        "yosys_bin": "/bin/echo",
                        "klayout_bin": "/bin/echo",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(adapter),
            "--request",
            str(request),
            "--result",
            str(result),
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(result.read_text(encoding="utf-8"))
    assert payload["status"] == "succeeded"
    assert {item["kind"] for item in payload["artifacts"]} == {"toolchain", "tool_log"}


def test_primetime_diagnostic_is_not_hidden_by_zero_exit(tmp_path: Path):
    adapter = TOOLKITS_ROOT / "synopsys-primetime" / "adapter.py"
    if not adapter.is_file():
        pytest.skip("server PrimeTime Toolkit is not installed")
    fake = tmp_path / "fake-pt"
    fake.write_text(
        "#!/bin/sh\necho 'Error: synthetic PT diagnostic'\nexit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    request = tmp_path / "request.json"
    result = tmp_path / "result.json"
    request.write_text(
        json.dumps(
            {
                "protocol_version": 1,
                "plugin": {
                    "plugin_id": "synopsys-primetime",
                    "plugin_version": "1.0.0",
                },
                "task": {
                    "inputs": {
                        "capability": "preflight",
                        "module": None,
                        "tool_path": str(fake),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(adapter),
            "--request",
            str(request),
            "--result",
            str(result),
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert completed.returncode != 0
    payload = json.loads(result.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["failure"]["category"] == "tool_error"
