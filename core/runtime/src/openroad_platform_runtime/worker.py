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

import argparse
import logging
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from openroad_platform_contracts import RuntimeStatus

from .runtime import WorkflowRuntime
from .store import InvalidTransition, RuntimeStore, RuntimeStoreError

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
        return {"reclaimed": self.reclaimed, "cancelled": self.cancelled,
                "advanced": self.advanced, "failed": self.failed}


class RuntimeWorker:
    """One worker process against one runtime store."""

    def __init__(
        self, store: RuntimeStore, runtime: WorkflowRuntime, *,
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

        report.reclaimed = len(self.store.reclaim_expired_attempts())
        report.cancelled = self._finish_abandoned_cancellations()

        for run_id in self.store.runnable_runs(limit=self.batch):
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
                LOGGER.warning("run %s could not be advanced: %s: %s",
                               run_id, type(exc).__name__, exc)
                report.failed += 1
                continue
            # Count work actually done.  Any check the worker could make for
            # itself -- status changed, attempt count grew -- also fires for a
            # worker that merely lost the race, which would report two workers
            # as having executed one attempt.
            if executed:
                report.advanced += 1
        return report

    def _finish_abandoned_cancellations(self) -> int:
        settled = 0
        for run_id in self.store.abandoned_cancellations(limit=self.batch):
            try:
                self.store.transition_run(
                    run_id, RuntimeStatus.CANCELLED,
                    reason="cancelled before the attempt started",
                )
            except InvalidTransition:
                continue
            settled += 1
        return settled

    # -- serving ----------------------------------------------------------

    def serve_forever(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        while not stop.is_set():
            report = self.cycle()
            if self.on_cycle is not None:
                self.on_cycle(report)
            if not report.did_work:
                stop.wait(self.idle_seconds)


def main(argv: list[str] | None = None) -> int:
    """Run a worker against a state root.

    The composition is deliberately the same as the kernel's, so a worker and
    the entry point cannot disagree about which store or which plugins they are
    using.
    """
    parser = argparse.ArgumentParser(prog="openroad-platform-worker")
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--plugins-root", default="plugins")
    parser.add_argument("--idle-seconds", type=float, default=DEFAULT_IDLE_SECONDS)
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--once", action="store_true",
                        help="run one cycle and exit; used by tests and cron")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    from openroad_platform_evaluator import PluginBackedEvaluator, resolve_evaluator
    from openroad_platform_registry import PluginRegistry

    from .adapter import ProcessAdapter
    from .runtime import RuntimeConfig

    state_root = Path(args.state_root).expanduser().resolve()
    store = RuntimeStore(state_root / "runtime.db")
    registry = PluginRegistry.from_directory(Path(args.plugins_root))
    evaluator = None
    try:
        evaluator = PluginBackedEvaluator(
            adapter=ProcessAdapter(), manifest=resolve_evaluator(registry))
    except Exception:  # noqa: BLE001 - a worker without an evaluator still runs
        LOGGER.warning("no protected evaluator is admitted; running without one")
    runtime = WorkflowRuntime(
        store, registry,
        config=RuntimeConfig(workspace_root=state_root / "runtime-workspaces"),
        protected_evaluator=evaluator,
    )
    worker = RuntimeWorker(store, runtime, idle_seconds=args.idle_seconds,
                           batch=args.batch)

    if args.once:
        report = worker.cycle()
        # JSON, because the caller is a script or an operator, not Python.
        import json
        print(json.dumps(report.to_dict(), sort_keys=True))
        return 0

    stop = threading.Event()

    def request_stop(signum, frame):  # noqa: ANN001, ARG001
        # A worker stops between attempts, never mid-attempt: an interrupted
        # attempt becomes a reclaimable lease, and killing the process would
        # orphan whatever it spawned.
        LOGGER.info("signal %s received; finishing the current cycle", signum)
        stop.set()

    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), request_stop)

    worker.serve_forever(stop)
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
