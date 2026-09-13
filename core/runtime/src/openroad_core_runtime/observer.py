"""Turn a plugin's output stream into durable stage events.

A control plane that has memorised one tool's stage names cannot host a second
tool without being edited.  The previous runtime did exactly that: it carried a
regular expression listing six hardcoded stage names belonging to one vendor.
Adding a second tool meant editing the kernel.

Here the observer reads the generic envelope from
``openroad_contracts.progress`` and the stage vocabulary stays inside the
plugin, travelling through as opaque data.  The kernel knows the shape of a
report and nothing about what is being reported.

Deduplication is deliberate: a tool that prints the same start marker on a retry
must not produce two ``stage.started`` events for one attempt.
"""

from __future__ import annotations

from typing import Callable

from openroad_contracts import (
    ContractError,
    ProgressPhase,
    decode_progress_line,
)

from .store import RuntimeStore


class ProgressObserver:
    """Collects stage events for one attempt.

    The event type is the platform's; the stage name is the plugin's.  Nothing
    in this class knows what a stage means.
    """

    def __init__(
        self,
        store: RuntimeStore,
        *,
        run_id: str,
        stage_run_id: str,
        attempt_id: str,
        marker: str,
        producer: str,
        downstream: Callable[[str], None] | None = None,
    ):
        self.store = store
        self.run_id = run_id
        self.stage_run_id = stage_run_id
        self.attempt_id = attempt_id
        self.marker = marker
        self.producer = producer
        self.downstream = downstream
        self._started: set[tuple[str, ...]] = set()
        self._finished: set[str] = set()
        self.malformed = 0

    def __call__(self, line: str) -> None:
        if self.downstream is not None:
            self.downstream(line)
        try:
            report = decode_progress_line(line, marker=self.marker)
        except ContractError:
            # A malformed envelope is a plugin bug worth surfacing, but it must
            # not abort a running experiment: the tool's own result is still
            # authoritative.  Count it so the run is auditable afterwards.
            self.malformed += 1
            return
        if report is None:
            return

        if report.phase is ProgressPhase.STARTED:
            key = (report.stage,)
            if key in self._started:
                return
            self._started.add(key)
            self.store.record_event(
                self.run_id, "stage.started", {"stage": report.stage},
                producer=self.producer, stage_run_id=self.stage_run_id,
                attempt_id=self.attempt_id,
            )
            return

        if report.stage in self._finished:
            return
        self._finished.add(report.stage)
        payload = {
            "stage": report.stage,
            "status": report.status.value if report.status else None,
        }
        if report.seconds is not None:
            payload["seconds"] = report.seconds
        if report.detail:
            payload["detail"] = report.detail
        self.store.record_event(
            self.run_id, "stage.finished", payload,
            producer=self.producer, stage_run_id=self.stage_run_id,
            attempt_id=self.attempt_id,
        )

    def record_summary(self) -> None:
        """Record how many envelopes could not be decoded, if any.

        Silence about a malformed report would let a plugin look healthier than
        it is, so the count goes into the run's evidence rather than a log line
        nobody reads.
        """
        if self.malformed:
            self.store.record_event(
                self.run_id, "progress.malformed",
                {"count": self.malformed, "marker": self.marker},
                producer=self.producer, stage_run_id=self.stage_run_id,
                attempt_id=self.attempt_id,
            )
