#!/usr/bin/env python3
"""Run and seal EDACraft ImplCraft admission through authoritative Runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "packages/contracts/src", ROOT / "packages/execution/src",
               ROOT / "packages/scheduler/src"):
    sys.path.insert(0, str(source))

from openroad_platform_contracts import RuntimeStatus  # noqa: E402
from openroad_platform_execution import (  # noqa: E402
    PluginRegistry, build_implcraft_task, implcraft_plugin_manifest,
)
from openroad_platform_scheduler import RuntimeStore, WorkflowRuntime  # noqa: E402


COMMIT = "739eee0f3ced8fc3cbb6f01b6cc89414758fd898"
EXPECTED_FAILURES = {
    "test_icc2_placement_with_tluplus",
    "test_innovus_placement_script",
    "test_flow_orchestrator_dry_run",
    "test_icc2_routing_si_analysis",
    "test_filter_physical_only_cells",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_root.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(output)
    output.mkdir(parents=True, exist_ok=True)
    source = ROOT / ".external-src/edacraft"
    python = ROOT / ".tools/venvs/implcraft/bin/python"
    if _git(source) != COMMIT or _git_status(source):
        raise RuntimeError("EDACraft fixed source is missing, mismatched, or dirty")
    lock = json.loads((ROOT / "integrations/edacraft_implcraft/environment.lock.json")
                      .read_text(encoding="utf-8"))
    if _sha(source / "LICENSE") != lock["license"]["sha256"]:
        raise RuntimeError("EDACraft license hash mismatch")
    if _sha(python) != lock["runtime"]["python_sha256"]:
        raise RuntimeError("ImplCraft Python hash mismatch")

    test_log = output / "upstream_tests.log"
    junit = output / "upstream_tests.xml"
    completed = subprocess.run(
        [str(python), "-m", "pytest", "-q", str(source / "ImplCraft/tests"),
         f"--junitxml={junit}"],
        cwd=ROOT, env={"PYTHONPATH": str(source / "ImplCraft"), "PATH": "/usr/bin:/bin"},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    test_log.write_text(completed.stdout, encoding="utf-8")
    tree = ET.parse(junit)
    cases = tree.findall(".//testcase")
    failed = {case.attrib["name"] for case in cases if case.find("failure") is not None}
    if len(cases) != 220 or failed != EXPECTED_FAILURES or completed.returncode != 1:
        raise RuntimeError(f"Unexpected ImplCraft upstream regression set: {failed}")
    _write(output / "upstream_test_summary.json", {
        "schema_version": 1, "total": len(cases), "passed": len(cases) - len(failed),
        "failed": sorted(failed), "expected_failures_unchanged": True,
    })

    rtl = output / "p11_top.v"
    rtl.write_text(
        "module p11_top(input clk, input rst_n, input [7:0] a, b, output reg [8:0] y);\n"
        "always @(posedge clk or negedge rst_n) if (!rst_n) y <= 0; else y <= a + b;\n"
        "endmodule\n", encoding="utf-8",
    )
    manifest = implcraft_plugin_manifest(source, python, expected_commit=COMMIT)
    live = Path("/tmp") / f"openroad-platform-p11-{uuid.uuid4().hex}"
    runtime = WorkflowRuntime(
        RuntimeStore(live / "runtime.db"), PluginRegistry([manifest]),
        workspace_root=output / "runtime-workspaces", worker_id="p11-acceptance",
    )
    run = runtime.submit(build_implcraft_task(
        rtl, project_id="p11-edacraft", design_id="p11-top", top="p11_top",
        clock="clk", stop_at="floorplan", task_id="p11-implcraft-dry-run",
    ))
    finished = runtime.execute_once(run.run_id)
    view = runtime.describe(run.run_id)
    _write(output / "runtime_run_snapshot.json", view)
    if finished.status is not RuntimeStatus.SUCCEEDED:
        raise RuntimeError("ImplCraft Runtime admission failed")
    attempt = view["stages"][0]["attempts"][0]
    inventory = []
    for item in attempt["artifacts"]:
        path = Path(attempt["workspace"]) / item["store_key"]
        actual = _sha(path)
        if actual != item["sha256"]:
            raise RuntimeError(f"ImplCraft artifact hash mismatch: {path}")
        inventory.append({**item, "sha256_recomputed": actual, "verified": True})
    _write(output / "artifact_inventory.json", inventory)
    snapshot_item = next(item for item in inventory if item["kind"] == "toolchain_snapshot")
    snapshot = json.loads((Path(attempt["workspace"]) / snapshot_item["store_key"])
                          .read_text(encoding="utf-8"))
    if snapshot["commercial_eda_executed"] or any(
        snapshot["commercial_tools_available"].values()
    ):
        raise RuntimeError("Commercial EDA capability gate is inconsistent")
    backup = output / "runtime.db.snapshot"
    with sqlite3.connect(runtime.store.path) as src, sqlite3.connect(backup) as dst:
        src.backup(dst)
    summary = {
        "schema_version": 1, "phase": "P11", "accepted": True,
        "run_id": run.run_id, "status": finished.status.value,
        "source_commit": COMMIT, "component": "ImplCraft 0.2.0",
        "capabilities": list(manifest.capabilities),
        "execution_class": "script-generation-only",
        "commercial_live_flow": False,
        "commercial_live_blocker": "required commercial EDA binaries/licenses unavailable",
        "artifact_count": len(inventory),
        "generated_script_count": sum(item["kind"] == "eda_script" for item in inventory),
        "runtime_db_under_tmp": str(runtime.store.path).startswith("/tmp/"),
        "upstream_tests": {"passed": 215, "expected_failures": 5},
    }
    _write(output / "acceptance_summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def _git(path: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()


def _git_status(path: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), "status", "--porcelain=v1"], text=True
    ).strip()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
