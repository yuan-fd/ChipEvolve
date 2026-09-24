"""Evidence report assembly from the platform client."""

from __future__ import annotations

from typing import Any

from openroad_platform_client import KernelClient, KernelError

from .catalog import artifact_selection, artifact_view
from .errors import QueryError

MAX_REPORT_RUNS = 20


def _source(metric: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": metric.get("run_id"),
        "attempt_id": metric.get("attempt_id"),
        "artifact": metric.get("artifact"),
        "parser": metric.get("parser"),
        "complete": bool(metric.get("complete")),
    }


def run_report(
    client: KernelClient, run_ids: list[str], *, question: str | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a sourced report without inventing missing values."""
    if not run_ids:
        raise QueryError(400, "at least one run_id is required")
    if len(run_ids) > MAX_REPORT_RUNS:
        raise QueryError(400, f"a report is limited to {MAX_REPORT_RUNS} runs")
    entries: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    for run_id in run_ids:
        try:
            detail = client.run(run_id)
            metrics = client.metrics(run_id)
            artifacts = client.artifacts(run_id)
        except KernelError as exc:
            entries.append({"run_id": run_id, "status": "unavailable",
                            "error": str(exc), "facts": [], "artifacts": [],
                            "raw_data": []})
            missing.append({"run_id": run_id, "reason": str(exc)})
            continue
        selected = artifact_selection(
            artifacts, path=(filters or {}).get("path"),
            sha256=(filters or {}).get("sha256"),
            kind=(filters or {}).get("kind"),
            artifact_id=(filters or {}).get("artifact_id"),
        )
        selected = [artifact_view(run_id, artifact) for artifact in selected]
        raw_data: list[dict[str, Any]] = []
        excerpt_id = (filters or {}).get("artifact_id")
        if excerpt_id:
            try:
                excerpt = client.artifact_excerpt(
                    run_id, excerpt_id,
                    offset=int((filters or {}).get("offset", 0)),
                    max_bytes=int((filters or {}).get("max_bytes", 8192)),
                )
                raw_data.append({"artifact_id": excerpt_id, "excerpt": excerpt})
            except (KernelError, ValueError) as exc:
                missing.append({"run_id": run_id, "reason": str(exc)})
        entries.append({
            "run_id": run_id,
            "design_id": detail.get("design_id"),
            "design_revision_id": detail.get("design_revision_id"),
            "status": detail.get("status"),
            "created_at": detail.get("created_at"),
            "ended_at": detail.get("ended_at"),
            "facts": [_fact(metric) for metric in metrics],
            "artifacts": selected,
            "raw_data": raw_data,
            "missing": ([{"kind": "artifact", "reason": "no matching artifact"}]
                        if not selected else []),
        })
    return {
        "question": question,
        "query": {"run_ids": run_ids, "filters": filters or {}},
        "runs": entries,
        "selected": [
            {"run_id": entry["run_id"],
             "design_id": entry.get("design_id"),
             "design_revision_id": entry.get("design_revision_id"),
             "artifact_ids": [a.get("artifact_id") for a in entry["artifacts"]]}
            for entry in entries if entry.get("status") != "unavailable"
        ],
        "limitations": {"missing": missing, "conflicts": [],
                        "truncated": [], "unknown": []},
        "evidence_policy": {
            "layers": ["raw_evidence", "extracted_fact", "recomputable_result",
                       "summary"],
            "unsourced_metrics_are_visible": True,
            "missing_values_are_not_filled": True,
            "source_required_for_claims": True,
        },
    }


def _fact(metric: dict[str, Any]) -> dict[str, Any]:
    source = _source(metric)
    complete = source["complete"]
    return {
        "name": metric.get("name"), "value": metric.get("value"),
        "unit": metric.get("unit"), "source": source,
        "evidence_level": "extracted_fact" if complete else "unknown",
        "confidence": "source_verified" if complete else "unsourced",
    }
