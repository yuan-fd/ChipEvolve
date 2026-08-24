from datetime import datetime, timezone

from apps.api.app import ApiState, _learning_identifier
from openroad_platform_contracts import RuntimeStatus, TaskSpec


def test_learning_identifiers_reject_web_placeholder_punctuation():
    assert _learning_identifier("orfs@registered", "unknown") == "orfs-registered"
    assert _learning_identifier(":orfs", "unknown") == "orfs"
    assert _learning_identifier("", "web-evidence-v1") == "web-evidence-v1"


def test_web_learning_collection_builds_valid_context_without_frontend_toolchain(tmp_path):
    state = ApiState(
        tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
        design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
        yosys_bin=tmp_path / "missing-yosys", runtime_db_path=tmp_path / "runtime.db",
 load_taiwei_plugin=False,
    )
    task = TaskSpec(
        "web-learning", "openroad-platform", "gcd", "orfs",
        inputs={"rtl": {"sha256": "a" * 64}},
        parameters={"platform": "nangate45", "target_stage": "finish"},
        labels={"owner_id": "local-user"},
    )
    run, stage = state.runtime_store.submit_plugin_run(task, plugin_version="1.0.0")
    attempt = state.runtime_store.start_attempt(
        stage.stage_run_id, worker_id="test", workspace=tmp_path / "workspace",
        lease_seconds=10,
    )
    artifact = state.runtime_store.register_artifact(
        attempt.attempt_id, kind="report", store_key="report.json",
        size_bytes=1, sha256="b" * 64,
    )
    state.runtime_store.register_metrics(attempt.attempt_id, [{
        "name": "area_um2", "value": 12.0, "unit": "um2",
        "source_artifact_id": artifact, "parser_id": "test",
        "parser_version": "1", "context": {"source": "observed"},
    }])
    state.runtime_store.finish_attempt(
        attempt.attempt_id, RuntimeStatus.SUCCEEDED, exit_code=0,
        now=datetime.now(timezone.utc),
    )
    receipt = state.collect_runtime_learning(run.run_id, {
        "owner_id": "local-user", "toolchain_id": "orfs@registered",
    })
    assert receipt["status"] == "admitted"
