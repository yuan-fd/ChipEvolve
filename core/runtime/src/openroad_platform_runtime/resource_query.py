"""Read-only views: what is reserved, what was used, and why a run has not started.

Deliberately a query adapter.  It does not schedule work and does not expose
SQLite rows; a different store or execution backend can provide the same small
read model later.

``waiting_for`` is the one that answers a question an operator asks out loud.
A run sitting in ``queued`` looks identical whether the machine is full, whether
no worker is running, or whether something is stuck -- and those need three
different responses from whoever is looking at it.  The platform knows which,
so it says.
"""

from __future__ import annotations

from typing import Any

from openroad_platform_contracts import RuntimeStatus

from .runtime import RuntimeConfig
from .store import RuntimeStore

#: Statuses in which a run has not started and nobody is working on it.
NOT_STARTED = frozenset({
    RuntimeStatus.QUEUED, RuntimeStatus.PREPARING, RuntimeStatus.RETRY_WAIT,
})


class ResourceQuery:
    def __init__(self, store: RuntimeStore, config: RuntimeConfig):
        self.store = store
        self.config = config

    def waiting_for(self, run_id: str) -> str | None:
        """Why this run has not started, in one line, or ``None`` if it has.

        Two answers, and neither is an error: the machine is full, or it is not
        and the run is simply next in line.  Saying "queued" and stopping there
        is what makes an operator open the database.
        """
        record = self.store.get_run(run_id)
        if record.status not in NOT_STARTED:
            return None

        need = self.config.reservation_for(record.task_spec)
        have_cpu, have_memory = self.store.resource_totals()
        budget_cpu = int(self.config.capacity_cpu_cores
                         * self.config.platform_fraction)
        budget_memory = int(self.config.capacity_memory_bytes
                            * self.config.platform_fraction)
        free_cpu = budget_cpu - have_cpu
        free_memory = budget_memory - have_memory

        if need.cpu_cores > free_cpu or need.memory_bytes > free_memory:
            return (
                f"resource: this run reserves {need.cpu_cores} cores and "
                f"{need.memory_bytes} bytes of memory, and the machine has "
                f"{max(free_cpu, 0)} cores and {max(free_memory, 0)} bytes free "
                f"within its budget"
            )
        return (
            "claimable: the machine has room for this run; it is waiting for a "
            "worker to claim it"
        )

    def run(self, run_id: str) -> dict[str, Any]:
        detail = self.store.describe_run(run_id)
        reserved_cpu, reserved_memory = self.store.resource_totals()
        attempts = [attempt for stage in detail.get("stages", [])
                    for attempt in stage.get("attempts", [])]
        task = self.store.get_run(run_id).task_spec
        requested = task.resources.to_dict() if task.resources is not None else None
        reserved = self.config.reservation_for(task)
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
            "would_reserve": reserved.to_dict(),
            "waiting_for": self.waiting_for(run_id),
            "attempts": [
                {
                    "attempt_id": attempt["attempt_id"],
                    "status": attempt["status"],
                    "usage": attempt.get("resource_usage"),
                }
                for attempt in attempts
            ],
        }
