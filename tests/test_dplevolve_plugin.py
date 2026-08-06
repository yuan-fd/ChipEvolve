from __future__ import annotations

import json
import platform
import sys
from pathlib import Path

import pytest

from openroad_platform_contracts import RuntimeStatus
from openroad_platform_execution import (
    PluginRegistry,
    build_dplevolve_audit_task,
    dplevolve_plugin_manifest,
    source_tree_digest,
)
from openroad_platform_scheduler import RuntimeStore, WorkflowRuntime


def _source(root: Path) -> Path:
    source = root / "dplevolve"
    gate = source / "scripts/repo/check_release_readiness.sh"
    gate.parent.mkdir(parents=True)
    gate.write_text("#!/usr/bin/env bash\nset -euo pipefail\necho '[release_readiness] OK'\n")
    (source / "LICENSE").write_text("BSD 3-Clause License\n")
    (source / "README.md").write_text("fixture\n")
    return source


def test_dplevolve_task_is_read_only_static_audit():
    task = build_dplevolve_audit_task(project_id="p15")
    assert task.parameters["allow_eda_execution"] is False
    assert task.parameters["allow_source_mutation"] is False
    assert task.labels["promotion"] == "human-only"


def test_dplevolve_source_digest_fails_closed(tmp_path):
    source = _source(tmp_path)
    digest, count = source_tree_digest(source)
    with pytest.raises(ValueError, match="source tree mismatch"):
        dplevolve_plugin_manifest(
            source, sys.executable, expected_tree_sha256="0" * 64,
            expected_file_count=count,
        )
    manifest = dplevolve_plugin_manifest(
        source, sys.executable, expected_tree_sha256=digest,
        expected_file_count=count,
    )
    assert manifest.environment["DPLEVOLVE_EXPECTED_TREE_SHA256"] == digest


def test_dplevolve_release_gate_is_runtime_recorded(tmp_path):
    source = _source(tmp_path)
    digest, count = source_tree_digest(source)
    manifest = dplevolve_plugin_manifest(
        source, sys.executable, expected_tree_sha256=digest,
        expected_file_count=count, default_timeout_seconds=30,
    )
    runtime = WorkflowRuntime(
        RuntimeStore(tmp_path / "runtime.db"), PluginRegistry([manifest]),
        workspace_root=tmp_path / "runs", worker_id="p15-fixture",
    )
    run = runtime.submit(build_dplevolve_audit_task(
        project_id="p15", timeout_seconds=30,
    ))
    completed = runtime.execute_once(run.run_id)
    assert completed.status is RuntimeStatus.SUCCEEDED
    description = runtime.describe(run.run_id)
    attempt = description["stages"][0]["attempts"][0]
    assert {item["kind"] for item in attempt["artifacts"]} == {
        "release_gate_log", "source_lock", "audit_report",
    }
    report_ref = next(item for item in attempt["artifacts"]
                      if item["kind"] == "audit_report")
    report = json.loads(
        (Path(attempt["workspace"]) / report_ref["store_key"]).read_text()
    )
    assert report["eda_executed"] is False
    assert report["candidate_promotion_applied"] is False


def test_repository_dplevolve_manifest_and_lock_are_consistent():
    root = Path(__file__).parents[1]
    manifest = PluginRegistry.from_directory(root / "integrations/dplevolve").resolve(
        "dplevolve", version="1.0.0", capability="eda.tool-evolve.audit",
        arch=platform.machine(),
    )
    lock = json.loads(
        (root / "integrations/dplevolve/source.lock.json").read_text()
    )
    assert manifest.environment["DPLEVOLVE_EXPECTED_COMMIT"] == lock["commit"]
    assert manifest.environment["DPLEVOLVE_EXPECTED_TREE_SHA256"] == lock[
        "content_manifest_sha256"
    ]
