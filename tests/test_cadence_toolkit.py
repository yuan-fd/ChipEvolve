"""Protocol smoke for the commercial Toolkit without requiring a license."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_innovus_adapter_preflight_contract_with_deployment_command(tmp_path: Path):
    request = tmp_path / "adapter_request.json"
    result = tmp_path / "adapter_result.json"
    request.write_text(
        json.dumps(
            {
                "protocol_version": 1,
                "plugin": {"plugin_id": "cadence-innovus", "plugin_version": "1.0.0"},
                "task": {
                    "inputs": {
                        "capability": "preflight",
                        "tool_path": "/bin/echo",
                        "module": None,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).parents[1] / "plugins/cadence-innovus/adapter.py"),
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
    assert {item["kind"] for item in payload["artifacts"]} == {
        "toolchain",
        "tool_log",
    }
    assert payload["metrics"][0]["context"]["source_artifact_store_key"] == (
        "toolchain.json"
    )
