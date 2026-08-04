from __future__ import annotations

from openroad_platform_contracts import RunRequest, RuntimeStatus
from openroad_platform_scheduler import JobStore, project_legacy_jobs


def test_legacy_job_projection_is_read_only_and_marks_unknown_provenance(tmp_path):
    rtl = tmp_path / "counter.v"
    rtl.write_text("module counter; endmodule\n", encoding="utf-8")
    path = tmp_path / "legacy.db"
    store = JobStore(path)
    job = store.submit(RunRequest(
        rtl_path=str(rtl), top="counter", platform="nangate45",
        labels={"project_id": "demo"},
    ))
    before = path.read_bytes()

    projections = project_legacy_jobs(path)

    assert path.read_bytes() == before
    assert len(projections) == 1
    projection = projections[0]
    assert projection.source_job_id == job.id
    assert projection.runtime_status is RuntimeStatus.QUEUED
    assert projection.task_spec.plugin_id == "legacy.orfs"
    assert projection.task_spec.inputs["rtl_path"] == str(rtl)
    assert projection.task_spec.project_id == "demo"
    assert projection.task_spec.design_id == "counter"
    assert projection.provenance["source_schema_version"] == "unversioned"
    assert projection.provenance["toolchain_revision"] == "unknown"
    assert projection.provenance["environment_digest"] == "unknown"


def test_legacy_preparing_status_projects_to_runtime_running(tmp_path):
    rtl = tmp_path / "top.v"
    rtl.write_text("module top; endmodule\n", encoding="utf-8")
    path = tmp_path / "legacy.db"
    store = JobStore(path)
    store.submit(RunRequest(rtl_path=str(rtl)))
    claimed = store.claim_next("worker")

    projection = project_legacy_jobs(path)[0]

    assert claimed is not None
    assert projection.source_status == "preparing"
    assert projection.runtime_status is RuntimeStatus.RUNNING
    assert projection.task_spec.design_id == "unknown"
