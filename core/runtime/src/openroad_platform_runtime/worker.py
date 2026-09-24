"""The worker that makes runs progress on their own.

Without this, a submitted run only advances when somebody calls
``execute_once`` by hand, which is not a platform -- it is a library with a
queue in front of it.

The cycle does three things, in this order:

1. **Reclaim expired leases.**  A worker that died mid-attempt left a row
   saying ``running``.  Marking it ``lost`` is evidence: the work may or may
   not have happened, and the platform says so instead of assuming either.
2. **Finish abandoned cancellations.**  A run cancelled while still queued has
   no running attempt to notice the request.  Without this it would sit in
   ``cancel_requested`` forever and the caller would never see it settle.
3. **Advance runnable runs.**  Claim a stage, execute one attempt, record
   everything.

The order matters.  Reclaiming before claiming means a stage whose lease just
expired becomes available in the same cycle rather than the next one.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from openroad_platform_contracts import RuntimeStatus

from .runtime import WorkflowRuntime
from .store import InvalidTransition, RuntimeStore
from .worker_presence import touch

LOGGER = logging.getLogger("openroad_platform_runtime.worker")

#: How long to sleep when there was nothing to do.  Short enough that a
#: submitted run starts promptly; long enough not to spin.
DEFAULT_IDLE_SECONDS = 0.5

#: How many runs one cycle will advance.  Bounded so a large backlog cannot
#: make a single cycle unbounded, which would delay cancellation and lease
#: reclamation for everything behind it.
DEFAULT_BATCH = 8


@dataclass
class CycleReport:
    """What one cycle did.  Returned rather than logged, so it is testable."""

    reclaimed: int = 0
    cancelled: int = 0
    advanced: int = 0
    failed: int = 0

    @property
    def did_work(self) -> bool:
        return bool(self.reclaimed or self.cancelled or self.advanced or self.failed)

    def to_dict(self) -> dict[str, int]:
        return {
            "reclaimed": self.reclaimed,
            "cancelled": self.cancelled,
            "advanced": self.advanced,
            "failed": self.failed,
        }


class RuntimeWorker:
    """One worker process against one runtime store."""

    def __init__(
        self,
        store: RuntimeStore,
        runtime: WorkflowRuntime,
        *,
        idle_seconds: float = DEFAULT_IDLE_SECONDS,
        batch: int = DEFAULT_BATCH,
        on_cycle: Callable[[CycleReport], None] | None = None,
    ):
        if idle_seconds < 0:
            raise ValueError("idle_seconds must not be negative")
        if batch < 1:
            raise ValueError("batch must be at least 1")
        self.store = store
        self.runtime = runtime
        self.idle_seconds = idle_seconds
        self.batch = batch
        self.on_cycle = on_cycle

    # -- one cycle --------------------------------------------------------

    def cycle(self) -> CycleReport:
        report = CycleReport()
        touch(self.store, self.runtime.config.worker_id)

        report.reclaimed = len(self.store.reclaim_expired_attempts())
        report.cancelled = self._finish_abandoned_cancellations()

        # Scan past blocked work.  The batch is a cap on work started, not a
        # cap on the number of queue rows inspected: a large first task must
        # not starve a small task behind it when capacity is temporarily full.
        advanced = 0
        for run_id in self.store.runnable_runs(limit=min(1000, self.batch * 16)):
            if advanced >= self.batch:
                break
            try:
                _, executed = self.runtime.execute_once_reporting(run_id)
            except InvalidTransition:
                # Another worker claimed the stage between the query and the
                # claim.  That is the lease doing its job, not a failure.
                continue
            except Exception as exc:  # noqa: BLE001 - a cycle must survive
                # One run this worker cannot advance must not stop the cycle:
                # everything behind it may be perfectly healthy.  The type and
                # message are logged, so this is reporting, not swallowing.
                LOGGER.warning(
                    "run %s could not be advanced: %s: %s",
                    run_id,
                    type(exc).__name__,
                    exc,
                )
                self.store.fail_queued_run(
                    run_id,
                    category="preflight_error",
                    message=f"{type(exc).__name__}: {exc}",
                )
                report.failed += 1
                continue
            # Count work actually done.  Any check the worker could make for
            # itself -- status changed, attempt count grew -- also fires for a
            # worker that merely lost the race, which would report two workers
            # as having executed one attempt.
            if executed:
                report.advanced += 1
                advanced += 1
        touch(self.store, self.runtime.config.worker_id)
        return report

    def _finish_abandoned_cancellations(self) -> int:
        settled = 0
        for run_id in self.store.abandoned_cancellations(limit=self.batch):
            try:
                self.store.transition_run(
                    run_id,
                    RuntimeStatus.CANCELLED,
                    reason="cancelled before the attempt started",
                )
            except InvalidTransition:
                continue
            settled += 1
        return settled

    # -- serving ----------------------------------------------------------

    def serve_forever(self, stop: threading.Event | None = None) -> None:
        """Serve with maintenance independent from long-running attempts."""
        stop = stop or threading.Event()
        active: set[str] = set()
        with ThreadPoolExecutor(max_workers=self.batch, thread_name_prefix="runtime-attempt") as pool:
            while not stop.is_set() or active:
                report = CycleReport()
                touch(self.store, self.runtime.config.worker_id)
                report.reclaimed = len(self.store.reclaim_expired_attempts())
                report.cancelled = self._finish_abandoned_cancellations()
                for run_id in self.store.runnable_runs(limit=min(1000, self.batch * 16)) if not stop.is_set() else ():
                    if len(active) >= self.batch or run_id in active:
                        continue
                    active.add(run_id)
                    pool.submit(self._run_async, run_id, active, report)
                if self.on_cycle is not None:
                    self.on_cycle(report)
                if not report.did_work and not active:
                    stop.wait(self.idle_seconds)
                else:
                    stop.wait(min(self.idle_seconds, 0.05))

    def _run_async(self, run_id: str, active: set[str], report: CycleReport) -> None:
        try:
            _, executed = self.runtime.execute_once_reporting(run_id)
            if executed:
                report.advanced += 1
        except InvalidTransition:
            pass
        except Exception as exc:  # noqa: BLE001 - isolate one attempt
            LOGGER.warning("run %s could not be advanced: %s: %s", run_id, type(exc).__name__, exc)
            try:
                if self.store.fail_queued_run(
                    run_id,
                    category="preflight_error",
                    message=f"{type(exc).__name__}: {exc}",
                ):
                    report.failed += 1
            except Exception:
                LOGGER.exception("could not record preflight failure for %s", run_id)
        finally:
            active.discard(run_id)
