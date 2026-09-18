#!/usr/bin/env python3
"""Cadence Innovus Toolkit adapter.

The adapter is intentionally a process boundary, not a Python API imported by
the kernel.  ``preflight`` captures a version/license check and ``script``
executes one Agent-staged Tcl file.  The foundation starts this adapter and
owns its entire descendant tree; this process only maps Toolkit semantics to
the external command and returns evidence.  Deployment configuration is left
to the shell and is not an adapter gate or protocol field.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _version(output: str) -> str:
    for line in output.splitlines():
        if "Version:" in line:
            return line.split("Version:", 1)[1].strip()
    match = re.search(r"\b(?:Innovus|INNOVUS)[^\n]*?\bv?([0-9]+\.[0-9]+[^\s]*)", output)
    return match.group(1) if match else "unparsed"


def _tool_environment(tool_path: Path, inputs: dict[str, Any]) -> dict[str, str]:
    values = inputs.get("tool_env", {})
    if not isinstance(values, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in values.items()
    ):
        raise ValueError("inputs.tool_env must be a string mapping")
    environment = os.environ.copy()
    environment.update(values)
    # Cadence's wrapper needs OA_HOME on installations that do not expose a
    # default share/oa link.  Discover a sibling OA directory without baking a
    # machine path into the Toolkit package.
    if "OA_HOME" not in environment:
        for parent in (tool_path.parent, tool_path.parent.parent):
            candidates = sorted(parent.glob("oa_v*"))
            if candidates:
                environment["OA_HOME"] = str(candidates[-1])
                break
    return environment


def _command(module: str | None, tool_path: str, args: list[str]) -> list[str]:
    if not module:
        return [tool_path, *args]
    # Login shells source the site Modules setup.  The module name and tool
    # path are positional parameters, so task data never becomes shell code.
    script = (
        "source /etc/profile.d/modules.sh 2>/dev/null || true; "
        'module load "$1"; shift; exec "$@"'
    )
    return ["/bin/bash", "-lc", script, "innovus-toolkit", module, tool_path, *args]


def _classify(returncode: int, output: str) -> dict[str, Any]:
    if returncode == 127:
        category = "tool_launch"
        retryable = False
    else:
        category = "tool_error"
        retryable = False
    return {
        "category": category,
        "message": f"Innovus exited with status {returncode}",
        "retryable": retryable,
    }


def _write_result(
    path: Path,
    *,
    started: str,
    status: str,
    exit_code: int,
    artifacts: list[dict[str, str]],
    metrics: list[dict[str, Any]],
    failure: dict[str, Any] | None,
    provenance: dict[str, Any],
) -> None:
    payload: dict[str, Any] = {
        "schema_version": 3,
        "status": status,
        "exit_code": exit_code,
        "started_at": started,
        "ended_at": now(),
        "artifacts": artifacts,
        "metrics": metrics,
        "provenance": provenance,
    }
    if failure is not None:
        payload["failure"] = failure
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    result_path = Path(args.result)
    workspace = result_path.parent.resolve()
    started = now()
    try:
        request = json.loads(Path(args.request).read_text(encoding="utf-8"))
        task = request["task"]
        inputs = task.get("inputs", {})
        capability = inputs.get("capability")
        if capability not in {"preflight", "script"}:
            raise ValueError("inputs.capability must be preflight or script")
        tool_path = Path(inputs.get("tool_path", "innovus")).expanduser()
        module = inputs.get("module", "cadence")
        if module is not None and not isinstance(module, str):
            raise ValueError("inputs.module must be a string or null")
        environment = _tool_environment(tool_path, inputs)
        if capability == "preflight":
            command_args = ["-version"]
        else:
            script_name = inputs.get("script_path")
            if not isinstance(script_name, str) or not script_name:
                raise ValueError("script capability requires inputs.script_path")
            script_path = (workspace / script_name).resolve()
            script_path.relative_to(workspace)
            if not script_path.is_file():
                raise ValueError(f"script does not exist: {script_name}")
            command_args = ["-batch", "-files", str(script_path)]
        command = _command(module, str(tool_path), command_args)
        completed = subprocess.run(
            command,
            cwd=workspace,
            env=environment,
            check=False,
            text=True,
            capture_output=True,
        )
        output = completed.stdout + completed.stderr
        (workspace / "tool.log").write_text(output, encoding="utf-8")
        version = _version(output)
        toolchain = {
            "toolkit_id": "cadence-innovus",
            "tool": "innovus",
            "resolved_executable": str(tool_path),
            "version": version,
            # Raw output remains in tool.log.  Keep protocol provenance to a
            # parsed version only so deployment license text is never copied
            # into toolchain metadata.
            "version_output": f"{version}\n",
            "module": module,
            "environment_keys": sorted(
                k for k in environment if k in {"OA_HOME", "PATH"}
            ),
        }
        (workspace / "toolchain.json").write_text(
            json.dumps(toolchain, indent=2), encoding="utf-8"
        )
        artifacts = [
            {"kind": "toolchain", "path": "toolchain.json"},
            {"kind": "tool_log", "path": "tool.log"},
        ]
        if capability == "script":
            receipt = {
                "capability": capability,
                "script_path": inputs["script_path"],
                "returncode": completed.returncode,
            }
            (workspace / "script_receipt.json").write_text(
                json.dumps(receipt, indent=2), encoding="utf-8"
            )
            artifacts.append({"kind": "script_receipt", "path": "script_receipt.json"})
        metrics = [
            {
                "name": "tool_version",
                "value": version,
                "context": {
                    "source_artifact_store_key": "toolchain.json",
                    "parser_id": "cadence-innovus",
                    "parser_version": "1.0.0",
                },
            }
        ]
        if completed.returncode:
            failure = _classify(completed.returncode, output)
            _write_result(
                result_path,
                started=started,
                status="failed",
                exit_code=completed.returncode,
                artifacts=artifacts,
                metrics=metrics,
                failure=failure,
                provenance=toolchain,
            )
            return completed.returncode
        _write_result(
            result_path,
            started=started,
            status="succeeded",
            exit_code=0,
            artifacts=artifacts,
            metrics=metrics,
            failure=None,
            provenance=toolchain,
        )
        return 0
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        _write_result(
            result_path,
            started=started,
            status="failed",
            exit_code=2,
            artifacts=[],
            metrics=[],
            failure={
                "category": "configuration_error",
                "message": str(exc),
                "retryable": False,
            },
            provenance={"toolkit_id": "cadence-innovus"},
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
