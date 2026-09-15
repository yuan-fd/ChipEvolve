"""Read-only resource view for agents and operators.

This module is deliberately a query adapter.  It does not schedule work and
does not expose SQLite rows; a different store or execution backend can provide
the same small read model later.
"""
from __future__ import annotations

from typing import Any

from .runtime import RuntimeConfig
from .store import RuntimeStore


class ResourceQuery:
    def __init__(self, store: RuntimeStore, config: RuntimeConfig):
        self.store = store
        self.config = config

    def run(self, run_id: str) -> dict[str, Any]:
        detail = self.store.describe_run(run_id)
        reserved_cpu, reserved_memory = self.store.resource_totals()
        attempts = [attempt for stage in detail.get("stages", [])
                    for attempt in stage.get("attempts", [])]
        task = self.store.get_run(run_id).task_spec
        requested = task.resources.to_dict() if task.resources is not None else None
        return {
            "capacity": {
                "cpu_cores": self.config.capacity_cpu_cores,
                "memory_bytes": self.config.capacity_memory_bytes,
                "platform_fraction": self.config.platform_fraction,
                "budget_cpu_cores": int(self.config.capacity_cpu_cores * self.config.platform_fraction),
                "budget_memory_bytes": int(self.config.capacity_memory_bytes * self.config.platform_fraction),
            },
            "reserved": {
                "cpu_cores": reserved_cpu,
                "memory_bytes": reserved_memory,
            },
            "run": run_id,
            "requested": requested,
            "attempts": [
                {
                    "attempt_id": attempt["attempt_id"],
                    "status": attempt["status"],
                    "requested": requested,
                    "usage": attempt.get("resource_usage"),
                }
                for attempt in attempts
            ],
        }
