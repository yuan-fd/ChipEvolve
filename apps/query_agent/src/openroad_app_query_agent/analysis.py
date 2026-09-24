"""Approval-aware planning for analysis runs.

The application prepares a human-readable request and delegates execution to a
registered capability through the platform client. It never executes a host
command and never edits the input run.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

from openroad_platform_client import KernelClient

from .evidence import QueryError


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise QueryError(400, f"{name} must be an object")
    return value


def _capability(client: KernelClient, plugin_id: str) -> dict[str, Any]:
    matches = [item for item in client.plugins()
               if item.get("plugin_id") == plugin_id]
    if len(matches) != 1:
        raise QueryError(400, f"registered analysis capability not found: {plugin_id}")
    capability = matches[0]
    if not capability.get("executable"):
        raise QueryError(409, f"analysis capability is not executable: {plugin_id}")
    return capability


def preview_analysis(client: KernelClient, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a request and return exactly what confirmation would approve."""
    task = _object(payload.get("task"), "task")
    plugin_id = task.get("plugin_id")
    if not isinstance(plugin_id, str) or not plugin_id:
        raise QueryError(400, "task.plugin_id is required")
    for field in ("project_id", "design_id"):
        if not isinstance(task.get(field), str) or not task[field]:
            raise QueryError(400, f"task.{field} is required")
    capability = _capability(client, plugin_id)
    source_run_id = payload.get("input_run_id")
    if source_run_id is not None and not isinstance(source_run_id, str):
        raise QueryError(400, "input_run_id must be a string")
    task_view = copy.deepcopy(task)
    parameters = _object(task_view.get("parameters") or {}, "task.parameters")
    labels = _object(task_view.get("labels") or {}, "task.labels")
    if source_run_id:
        labels["analysis_of_run_id"] = source_run_id
    task_view["labels"] = labels
    return {
        "approval_required": True,
        "confirmed": False,
        "tool": {
            "plugin_id": capability.get("plugin_id"),
            "plugin_version": capability.get("plugin_version"),
            "capabilities": capability.get("capabilities", []),
            "declared": capability.get("declared", {}),
        },
        "command": parameters.get("command"),
        "inputs": task_view.get("staged_inputs", []),
        "estimated": {
            "timeout_seconds": task_view.get("timeout_seconds"),
            "resources": task_view.get("resources"),
        },
        "outputs": task_view.get("expected_artifacts", []),
        "target": {
            "project_id": task_view.get("project_id"),
            "design_id": task_view.get("design_id"),
            "design_revision_id": task_view.get("design_revision_id"),
            "run_type": "analysis",
            "input_run_id": source_run_id,
        },
        "task": task_view,
    }


def submit_analysis(client: KernelClient, payload: dict[str, Any]) -> dict[str, Any]:
    """Submit only a request carrying an explicit caller confirmation."""
    if payload.get("confirm") is not True:
        raise QueryError(409, "analysis execution requires confirm=true")
    preview = preview_analysis(client, payload)
    task = copy.deepcopy(preview["task"])
    if not task.get("task_id"):
        task["task_id"] = f"analysis-{uuid.uuid4().hex}"
    result = client.submit(task, plugin_version=preview["tool"]["plugin_version"])
    return {
        "approval": {
            "confirmed": True,
            "tool": preview["tool"],
            "inputs": preview["inputs"],
            "target": preview["target"],
        },
        "run": result,
    }
