"""Design/run and artifact catalogue views."""

from __future__ import annotations

from typing import Any

from .errors import QueryError

MAX_RUNS = 100


def integer(value: str | None, name: str, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise QueryError(400, f"{name} must be an integer") from exc


def limit(value: str | None, default: int = 20) -> int:
    result = integer(value, "limit", default)
    if not 1 <= result <= MAX_RUNS:
        raise QueryError(400, f"limit must be between 1 and {MAX_RUNS}")
    return result


def run_filters(query: dict[str, list[str]]) -> dict[str, Any]:
    values = {key: items[0] for key, items in query.items() if items}
    filters: dict[str, Any] = {
        name: values[name] for name in
        ("project_id", "design_id", "plugin_id", "status") if values.get(name)
    }
    filters["limit"] = limit(values.get("limit"))
    filters["offset"] = integer(values.get("offset"), "offset", 0)
    if filters["offset"] < 0:
        raise QueryError(400, "offset must not be negative")
    return filters


def designs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str | None], dict[str, Any]] = {}
    for run in runs:
        key = (str(run.get("project_id", "")), str(run.get("design_id", "")),
               run.get("design_revision_id"))
        entry = grouped.setdefault(key, {
            "project_id": key[0], "design_id": key[1],
            "design_revision_id": key[2], "runs": [],
        })
        entry["runs"].append(run)
    for entry in grouped.values():
        entry["run_count"] = len(entry["runs"])
        entry["latest_run"] = entry["runs"][0] if entry["runs"] else None
    return list(grouped.values())


def artifact_selection(
    artifacts: list[dict[str, Any]], *, path: str | None = None,
    sha256: str | None = None, kind: str | None = None,
    artifact_id: str | None = None,
) -> list[dict[str, Any]]:
    """Apply catalogue filters to an already authorized artifact list."""
    return [
        artifact for artifact in artifacts
        if (path is None or artifact.get("store_key") == path)
        and (sha256 is None or artifact.get("sha256") == sha256)
        and (kind is None or artifact.get("kind") == kind)
        and (artifact_id is None or artifact.get("artifact_id") == artifact_id)
    ]


def artifact_view(run_id: str, artifact: dict[str, Any]) -> dict[str, Any]:
    """Expose one stable, host-independent reference to an artifact."""
    metadata = artifact.get("metadata") or {}
    artifact_id = artifact.get("artifact_id")
    logical_path = metadata.get("logical_path") or artifact.get("store_key")
    return {
        **artifact,
        "logical_path": logical_path,
        "category": metadata.get("category"),
        "stage": artifact.get("stage_key") or metadata.get("stage"),
        "format": metadata.get("format") or artifact.get("media_type"),
        "source": {"run_id": run_id, "attempt_id": artifact.get("attempt_id"),
                   "artifact_id": artifact_id},
        "integrity": {"sha256": artifact.get("sha256"),
                       "size_bytes": artifact.get("size_bytes"),
                       "registered": True},
        "access": {
            "excerpt": f"/artifacts/{run_id}/{artifact_id}/excerpt",
            "download": f"/artifacts/{run_id}/{artifact_id}/download",
        },
    }
