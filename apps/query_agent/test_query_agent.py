"""Application-level tests for evidence planning and report assembly."""

from __future__ import annotations

from typing import Any

from openroad_app_query_agent.analysis import preview_analysis, submit_analysis
from openroad_app_query_agent.evidence import (
    QueryError,
    artifact_view,
    designs,
    plan_question,
    run_filters,
    run_report,
    structured_query,
)
from openroad_platform_client import KernelError


class FakeClient:
    def __init__(self) -> None:
        self.run_rows = [
            {"run_id": "run-a", "project_id": "p", "design_id": "chip",
             "design_revision_id": "rev-1", "status": "succeeded"},
            {"run_id": "run-b", "project_id": "p", "design_id": "chip",
             "design_revision_id": "rev-2", "status": "failed"},
        ]

    def runs(self, **filters: Any) -> list[dict[str, Any]]:
        rows = self.run_rows
        if filters.get("design_id"):
            rows = [r for r in rows if r["design_id"] == filters["design_id"]]
        return rows

    def run(self, run_id: str) -> dict[str, Any]:
        if run_id == "missing":
            raise KernelError("run not found", status=404)
        return {**next(row for row in self.run_rows if row["run_id"] == run_id),
                "created_at": "2026-01-01T00:00:00Z", "ended_at": None}

    def metrics(self, run_id: str) -> list[dict[str, Any]]:
        return [{"name": "area", "value": 10, "unit": "um2", "run_id": run_id,
                 "attempt_id": "attempt-a", "artifact": None,
                 "parser": None, "complete": False}]

    def artifacts(self, run_id: str) -> list[dict[str, Any]]:
        return [{"artifact_id": f"artifact-{run_id}", "kind": "report",
                 "store_key": "reports/summary.rpt", "sha256": "a" * 64,
                 "size_bytes": 10}]

    def plugins(self) -> list[dict[str, Any]]:
        return [{"plugin_id": "reporter", "plugin_version": "1",
                 "capabilities": ["report"], "executable": True}]

    def submit(self, task: dict[str, Any], *, plugin_version: str) -> dict[str, Any]:
        return {"run": {"run_id": task["task_id"], "plugin_version": plugin_version}}

    def artifact_excerpt(self, run_id: str, artifact_id: str, *,
                         offset: int, max_bytes: int) -> dict[str, Any]:
        return {"artifact_id": artifact_id, "run_id": run_id, "offset": offset,
                "max_bytes": max_bytes, "text": "area 10", "truncated": False}


def test_designs_keep_revisions_separate() -> None:
    grouped = designs(FakeClient().run_rows)
    assert {(item["design_id"], item["design_revision_id"]) for item in grouped} == {
        ("chip", "rev-1"), ("chip", "rev-2")
    }


def test_report_keeps_unsourced_metric_visible() -> None:
    report = run_report(FakeClient(), ["run-a"])
    fact = report["runs"][0]["facts"][0]
    assert fact["value"] == 10
    assert fact["source"]["complete"] is False
    assert report["evidence_policy"]["source_required_for_claims"] is True


def test_artifact_view_has_stable_access_and_integrity_fields() -> None:
    view = artifact_view("run-a", {"artifact_id": "artifact-a",
                                    "store_key": "reports/final.rpt",
                                    "sha256": "a" * 64, "size_bytes": 4,
                                    "metadata": {"category": "report"}})
    assert view["logical_path"] == "reports/final.rpt"
    assert view["source"]["run_id"] == "run-a"
    assert view["integrity"]["registered"] is True
    assert view["access"]["download"].endswith("/download")


def test_missing_run_is_explicitly_unavailable() -> None:
    report = run_report(FakeClient(), ["missing"])
    assert report["runs"][0]["status"] == "unavailable"
    assert report["runs"][0]["facts"] == []


def test_question_planner_uses_same_report_path_for_design_queries() -> None:
    answer = plan_question(FakeClient(), "show design chip")
    assert answer["interpretation"]["kind"] == "design_runs"
    assert len(answer["report"]["runs"]) == 2


def test_structured_query_uses_the_same_sourced_report() -> None:
    answer = structured_query(FakeClient(), {"run_ids": ["run-a"],
                                               "kind": "report"})
    assert answer["interpretation"]["kind"] == "runs"
    assert answer["report"]["selected"][0]["artifact_ids"] == ["artifact-run-a"]


def test_structured_report_can_include_a_bounded_raw_excerpt() -> None:
    answer = structured_query(FakeClient(), {"run_ids": ["run-a"],
                                               "artifact_id": "artifact-run-a",
                                               "max_bytes": 32})
    assert answer["report"]["runs"][0]["raw_data"][0]["excerpt"]["text"] == "area 10"


def test_filters_reject_invalid_pagination() -> None:
    try:
        run_filters({"limit": ["0"]})
    except QueryError as exc:
        assert exc.status == 400
    else:
        raise AssertionError("invalid limit was accepted")


def test_analysis_preview_exposes_inputs_and_requires_confirmation() -> None:
    payload = {"input_run_id": "run-a", "task": {
        "task_id": "analysis-a", "project_id": "p", "design_id": "chip",
        "plugin_id": "reporter", "parameters": {"command": "report"},
        "timeout_seconds": 30, "expected_artifacts": ["report"],
    }}
    preview = preview_analysis(FakeClient(), payload)
    assert preview["approval_required"] is True
    assert preview["target"]["run_type"] == "analysis"
    assert preview["task"]["labels"]["analysis_of_run_id"] == "run-a"


def test_analysis_submission_needs_explicit_confirmation() -> None:
    payload = {"task": {"task_id": "analysis-a", "project_id": "p",
                         "design_id": "chip", "plugin_id": "reporter"}}
    try:
        submit_analysis(FakeClient(), payload)
    except QueryError as exc:
        assert exc.status == 409
    else:
        raise AssertionError("analysis ran without confirmation")


def test_confirmed_analysis_is_a_separate_run() -> None:
    payload = {"confirm": True, "input_run_id": "run-a", "task": {
        "task_id": "analysis-a", "project_id": "p", "design_id": "chip",
        "plugin_id": "reporter",
    }}
    result = submit_analysis(FakeClient(), payload)
    assert result["approval"]["confirmed"] is True
    assert result["run"]["run"]["run_id"] == "analysis-a"
