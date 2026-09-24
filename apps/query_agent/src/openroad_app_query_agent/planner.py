"""Natural-language and structured query planning."""

from __future__ import annotations

import re
from typing import Any

from openroad_platform_client import KernelClient

from .catalog import MAX_RUNS, integer, limit
from .errors import QueryError
from .report import run_report


def plan_question(client: KernelClient, question: str) -> dict[str, Any]:
    """Turn a small auditable question vocabulary into a sourced report."""
    text = question.strip()
    if not text:
        raise QueryError(400, "question is required")
    run_match = re.search(r"\b(run-[a-f0-9]+)\b", text, re.IGNORECASE)
    if run_match:
        run_ids = [run_match.group(1)]
        return {"interpretation": {"kind": "run", "run_ids": run_ids},
                "report": run_report(client, run_ids, question=text)}
    design_match = re.search(
        r"(?:design|设计)\s*[:=]?\s*([A-Za-z0-9_.-]+)", text, re.IGNORECASE
    )
    filters: dict[str, Any] = {"limit": MAX_RUNS}
    if design_match:
        filters["design_id"] = design_match.group(1)
    runs = client.runs(**filters)
    return {
        "interpretation": {
            "kind": "design_runs" if design_match else "recent_runs",
            "filters": filters,
        },
        "report": run_report(
            client, [str(run["run_id"]) for run in runs], question=text,
            filters=filters,
        ),
    }


def structured_query(client: KernelClient, payload: dict[str, Any]) -> dict[str, Any]:
    """Plan the same evidence report from an explicit query object."""
    if not payload:
        raise QueryError(400, "query requires question or structured filters")
    run_ids = payload.get("run_ids")
    if run_ids is not None:
        if (not isinstance(run_ids, list)
                or not all(isinstance(run_id, str) and run_id for run_id in run_ids)):
            raise QueryError(400, "run_ids must be a list of non-empty strings")
        return {
            "interpretation": {"kind": "runs", "run_ids": run_ids},
            "report": run_report(client, run_ids, filters=payload),
        }
    allowed = ("project_id", "design_id", "plugin_id", "status")
    filters = {name: payload[name] for name in allowed if payload.get(name)}
    filters["limit"] = limit(str(payload.get("limit", MAX_RUNS)), MAX_RUNS)
    filters["offset"] = integer(str(payload.get("offset", 0)), "offset", 0)
    if filters["offset"] < 0:
        raise QueryError(400, "offset must not be negative")
    runs = client.runs(**filters)
    selected = [str(run["run_id"]) for run in runs]
    return {
        "interpretation": {"kind": "filtered_runs", "filters": filters},
        "report": run_report(client, selected, filters=payload),
    }
