"""Read-only projection of the pre-v1 ``jobs`` table into v1 contracts."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openroad_platform_contracts import RunRequest, RuntimeStatus, TaskSpec


UNKNOWN_PROVENANCE_FIELDS = (
    "source_revision",
    "toolchain_revision",
    "pdk_revision",
    "environment_digest",
    "adapter_version",
)


@dataclass(frozen=True)
class LegacyJobProjection:
    """A non-authoritative view; importing it requires an explicit later step."""

    source_job_id: str
    source_status: str
    runtime_status: RuntimeStatus
    task_spec: TaskSpec
    result: dict[str, Any] | None
    error: str | None
    created_at: str
    updated_at: str
    provenance: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_job_id": self.source_job_id,
            "source_status": self.source_status,
            "runtime_status": self.runtime_status.value,
            "task_spec": self.task_spec.to_dict(),
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "provenance": dict(self.provenance),
        }


def project_legacy_jobs(
    path: str | Path,
    *,
    limit: int = 500,
) -> tuple[LegacyJobProjection, ...]:
    """Read legacy jobs without initializing, migrating, or writing their database."""

    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    bounded_limit = max(1, min(int(limit), 10_000))
    uri = f"{source.as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=30, isolation_level=None) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        rows = connection.execute(
            "SELECT * FROM jobs ORDER BY created_at, id LIMIT ?", (bounded_limit,)
        ).fetchall()
    return tuple(_project(row) for row in rows)


def _project(row: sqlite3.Row) -> LegacyJobProjection:
    request = RunRequest.from_dict(json.loads(row["request_json"]))
    labels = dict(request.labels)
    project_id = labels.get("project_id") or "unknown"
    design_id = request.top or "unknown"
    task = TaskSpec(
        task_id=f"legacy:{row['id']}",
        project_id=_identifier_or_unknown(project_id),
        design_id=_identifier_or_unknown(design_id),
        plugin_id="legacy.orfs",
        inputs={
            "rtl_path": request.rtl_path,
            "top": request.top,
            "clock": request.clock,
            "platform": request.platform,
            "target_stage": request.target_stage.value,
        },
        parameters={
            "clock_period_ns": request.clock_period_ns,
            "core_utilization_pct": request.core_utilization_pct,
            "place_density": request.place_density,
        },
        timeout_seconds=request.stage_timeout_seconds,
        max_attempts=1,
        labels={**labels, "source_schema": "legacy_jobs"},
    )
    task.validate()
    provenance = {name: "unknown" for name in UNKNOWN_PROVENANCE_FIELDS}
    provenance.update({
        "source": "legacy_jobs",
        "source_job_id": row["id"],
        "source_schema_version": "unversioned",
    })
    return LegacyJobProjection(
        source_job_id=row["id"],
        source_status=row["status"],
        runtime_status=_runtime_status(row["status"]),
        task_spec=task,
        result=json.loads(row["result_json"]) if row["result_json"] else None,
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        provenance=provenance,
    )


def _runtime_status(value: str) -> RuntimeStatus:
    if value == "preparing":
        return RuntimeStatus.RUNNING
    try:
        return RuntimeStatus(value)
    except ValueError as exc:
        raise ValueError(f"Unsupported legacy job status: {value!r}") from exc


def _identifier_or_unknown(value: str) -> str:
    candidate = value.strip()
    if candidate and len(candidate) <= 128 and all(
        character.isalnum() or character in "_.:-" for character in candidate
    ) and candidate[0].isalnum():
        return candidate
    return "unknown"
