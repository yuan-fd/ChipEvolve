"""The whole path, end to end: directory -> registry -> runtime -> evidence.

This is the test that proves the architecture is real rather than declared.

The previous platform had a plugin registry that production never called -- it
wired ten manifests by hand inside its API class -- and a plugin protocol whose
adapters mostly lived inside the platform package.  Here a capability is
discovered from a directory, admitted from its own evidence, executed as a
separate process, and its output becomes durable, hash-verified evidence.

Nothing in the kernel knows the word "example-reporter". That is the point.
"""

from __future__ import annotations

import json
from pathlib import Path

from openroad_platform_contracts import RuntimeStatus, TaskSpec
from openroad_platform_registry import PluginRegistry
from openroad_platform_runtime import (
    ProcessAdapter,
    RuntimeConfig,
    RuntimeStore,
    WorkflowRuntime,
)
from openroad_platform_runtime.guardian import ProcessGuardian

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_ROOT = REPO_ROOT / "plugins"
ADMISSIONS_ROOT = REPO_ROOT / "admissions"


def build(tmp_path: Path):
    registry = PluginRegistry.from_directory(PLUGINS_ROOT, admissions_root=ADMISSIONS_ROOT)
    store = RuntimeStore(tmp_path / "runtime.db")
    runtime = WorkflowRuntime(
        store, registry,
        config=RuntimeConfig(workspace_root=tmp_path / "workspaces",
                             worker_id="integration-test"),
        adapter=ProcessAdapter(
            ProcessGuardian(poll_interval=0.02, terminate_grace=1.0)
        ),
    )
    return registry, store, runtime


def test_the_example_plugin_is_discovered_and_admitted():
    registry = PluginRegistry.from_directory(PLUGINS_ROOT, admissions_root=ADMISSIONS_ROOT)
    catalogue = {row["plugin_id"]: row for row in registry.catalogue()}
    assert "example-reporter" in catalogue
    entry = catalogue["example-reporter"]
    assert entry["executable"] is True
    assert entry["capabilities"] == ["report.summarize"]
    # The adapter path was resolved against the plugin's own directory, not
    # against the platform source tree.
    assert Path(entry["adapter_entry"][1]).parent.name == "example"


def test_a_discovered_capability_runs_end_to_end(tmp_path):
    registry, store, runtime = build(tmp_path)
    try:
        run = runtime.submit(TaskSpec(
            task_id="integration-1", project_id="demo", design_id="demo",
            plugin_id="example-reporter",
            inputs={"records": [1, 2, 3, 4]},
            timeout_seconds=30,
        ))
        finished = runtime.execute_once(run.run_id)
        assert finished.status is RuntimeStatus.SUCCEEDED

        view = runtime.describe(run.run_id)
        attempt = view["stages"][0]["attempts"][0]
        assert attempt["status"] == "succeeded"

        # Artifacts are registered with a hash the platform measured itself.
        by_kind = {a["kind"]: a for a in attempt["artifacts"]}
        assert set(by_kind) == {"summary", "log"}
        summary_path = Path(attempt["workspace"]) / by_kind["summary"]["store_key"]
        import hashlib

        assert by_kind["summary"]["sha256"] == hashlib.sha256(
            summary_path.read_bytes()
        ).hexdigest()

        # Metrics point at the artifact they were read from.
        metrics = {m["name"]: m for m in attempt["metrics"]}
        assert metrics["record_count"]["value"] == 4
        assert metrics["mean"]["value"] == 2.5
        for metric in metrics.values():
            assert metric["source_artifact_id"] == by_kind["summary"]["artifact_id"]
            assert metric["parser_id"] == "example-reporter"

        # Stage progress became durable events, and the kernel never knew the
        # stage names: they came from the plugin's own envelope.
        stage_events = [
            e for e in store.list_events(run.run_id)
            if e.event_type.startswith("stage.")
        ]
        stages_started = [
            e.payload["stage"] for e in stage_events
            if e.event_type == "stage.started"
        ]
        assert stages_started == ["validate", "summarize"]

        # The artifact is readable back through the runtime authority, which
        # re-verifies the hash rather than trusting the row.
        excerpt = runtime.read_artifact_excerpt(
            run.run_id, by_kind["summary"]["artifact_id"],
            offset=0, max_bytes=4096,
        )
        payload = json.loads(excerpt["text"])
        assert payload["count"] == 4
        assert payload["mean"] == 2.5
    finally:
        store.close()


def test_a_bad_task_fails_with_platform_evidence_preserved(tmp_path):
    registry, store, runtime = build(tmp_path)
    try:
        run = runtime.submit(TaskSpec(
            task_id="integration-2", project_id="demo", design_id="demo",
            plugin_id="example-reporter",
            inputs={"records": "not-a-list"},
            timeout_seconds=30,
        ))
        finished = runtime.execute_once(run.run_id)
        assert finished.status is RuntimeStatus.FAILED

        attempt = runtime.describe(run.run_id)["stages"][0]["attempts"][0]
        assert attempt["status"] == "failed"
        # No domain artifact is claimed, but platform evidence preserves the
        # request, result and log needed to diagnose the rejected input.
        assert {a["kind"] for a in attempt["artifacts"]} == {
            "runtime_evidence_request", "runtime_evidence_result", "runtime_evidence_log",
        }
        assert attempt["metrics"] == []
    finally:
        store.close()


def test_the_registry_and_runtime_compose_without_the_kernel_naming_a_plugin(
    tmp_path,
):
    """A structural assertion, stated as a test.

    The kernel packages must execute this plugin while containing no reference
    to it.  If that ever stops being true, G1 would fail too -- this makes the
    intent explicit at the integration level.
    """
    kernel_sources = [
        path for path in (REPO_ROOT / "core").rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    assert kernel_sources, "expected to find kernel sources"
    for path in kernel_sources:
        assert "example-reporter" not in path.read_text(encoding="utf-8")
        assert "report.summarize" not in path.read_text(encoding="utf-8")
