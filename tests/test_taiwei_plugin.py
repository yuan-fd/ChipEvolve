from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path

import pytest

from openroad_platform_contracts import RuntimeStatus
from openroad_platform_execution import (
    PluginRegistry, TaiWeiToolchainProfile, build_taiwei_task,
    taiwei_plugin_manifest,
)
from openroad_platform_scheduler import RuntimeStore, WorkflowRuntime


def commit_repo(path: Path, files: dict[str, str]) -> str:
    path.mkdir(parents=True)
    for name, content in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        if name.endswith((".py", ".sh")):
            target.chmod(0o755)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "-qm", "fixture"], check=True)
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()


def fixture_environment(tmp_path: Path):
    source = tmp_path / "taiwei"
    script = '''#!/usr/bin/env python3
import argparse,json
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument("--flow"); p.add_argument("--tech"); p.add_argument("--case"); p.add_argument("--run-only",action="store_true"); p.add_argument("--status-interval"); a=p.parse_args()
assert (a.flow,a.tech,a.case)==("ord","asap7_3D","gcd")
for name,data in (("reports/openroad_eval.json",json.dumps({"wns_ns":0.1,"hbt_count":4})),("logs/final_summary.txt","ok\\n"),("results/final.gds","GDSII"),("results/6_final.def","DEF"),("results/6_final.odb","ODB"),("results/6_final.v","module gcd; endmodule\\n"),("reports/final_3d.png","PNG"),("platforms/nangate45/gds/library.gds","NOT-A-RUN-RESULT")):
 path=Path(name); path.parent.mkdir(parents=True,exist_ok=True); path.write_text(data)
'''
    source_commit = commit_repo(source, {"run_experiments.py": script})
    orfs = tmp_path / "orfs-research"
    orfs_commit = commit_repo(orfs, {"README.md": "fixed ORFS fixture\n"})
    openroad = orfs / "tools/OpenROAD"
    openroad_commit = commit_repo(openroad, {"README.md": "fixed OpenROAD fixture\n"})
    # The nested repository made the parent dirty; record its gitlink.
    subprocess.run(["git", "-C", str(orfs), "add", "tools/OpenROAD"], check=True)
    subprocess.run(["git", "-C", str(orfs), "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "-qm", "pin openroad"], check=True)
    orfs_commit = subprocess.check_output(["git", "-C", str(orfs), "rev-parse", "HEAD"], text=True).strip()
    bin_dir = orfs / "tools/install/bin"
    bin_dir.mkdir(parents=True)
    for name in ("openroad", "yosys"):
        path = bin_dir / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
    subprocess.run(["git", "-C", str(orfs), "add", "tools/install/bin"], check=True)
    subprocess.run(["git", "-C", str(orfs), "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "-qm", "fixture tools"], check=True)
    orfs_commit = subprocess.check_output(["git", "-C", str(orfs), "rev-parse", "HEAD"], text=True).strip()
    profile = TaiWeiToolchainProfile(orfs, bin_dir / "openroad", bin_dir / "yosys",
                                    orfs_commit=orfs_commit, openroad_commit=openroad_commit)
    return source, source_commit, profile


def test_taiwei_task_restricts_case_and_budget():
    task = build_taiwei_task(project_id="p8", timeout_seconds=60)
    assert task.inputs == {"flow": "ord", "tech": "asap7_3D", "case": "gcd"}
    assert task.labels["real_3d_required"] == "true"
    with pytest.raises(ValueError, match="gcd"):
        build_taiwei_task(project_id="p8", design_id="aes")


def test_toolchain_commit_mismatch_fails_closed(tmp_path):
    source, source_commit, profile = fixture_environment(tmp_path)
    wrong = TaiWeiToolchainProfile(profile.orfs_root, profile.openroad_bin,
                                   profile.yosys_bin, orfs_commit="0" * 40,
                                   openroad_commit=profile.openroad_commit)
    with pytest.raises(ValueError, match="commit mismatch"):
        taiwei_plugin_manifest(source, wrong, expected_commit=source_commit)


def test_black_box_adapter_stages_source_and_registers_3d_artifacts(tmp_path):
    source, source_commit, profile = fixture_environment(tmp_path)
    manifest = taiwei_plugin_manifest(source, profile, python_executable=sys.executable,
                                      expected_commit=source_commit,
                                      default_timeout_seconds=30)
    runtime = WorkflowRuntime(RuntimeStore(tmp_path / "runtime.db"),
                              PluginRegistry([manifest]),
                              workspace_root=tmp_path / "runs", worker_id="p8-fixture")
    run = runtime.submit(build_taiwei_task(project_id="p8", timeout_seconds=30))
    completed = runtime.execute_once(run.run_id)
    view = runtime.describe(run.run_id)
    attempt = view["stages"][0]["attempts"][0]
    assert completed.status is RuntimeStatus.SUCCEEDED
    assert {item["kind"] for item in attempt["artifacts"]} == {
        "three_d_eval", "three_d_summary", "gds", "def", "odb", "netlist", "three_d_view",
        "toolchain_snapshot", "log",
    }
    assert not (source / "reports").exists()
    snapshot = next(item for item in attempt["artifacts"]
                    if item["kind"] == "toolchain_snapshot")
    payload = json.loads((Path(attempt["workspace"]) / snapshot["store_key"]).read_text())
    assert payload["orfs_commit"] == profile.orfs_commit
