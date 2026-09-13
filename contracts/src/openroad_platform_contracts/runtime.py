"""Run status, attempt lifecycle, and the durable event record.

These are the platform's vocabulary for "what happened".  Runtime is the only
writer of a run's status; nobody else -- not a plugin, not an app, not a model
-- may declare a run successful.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .version import (
    instantiate,
    ContractError,
    SCHEMA_VERSION,
    known_payload,
    primitive,
    validate_identifier,
    validate_mapping,
    validate_version,
)


class RuntimeStatus(str, Enum):
    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    LOST = "lost"


TERMINAL_RUNTIME_STATUSES = frozenset({
    RuntimeStatus.SUCCEEDED,
    RuntimeStatus.FAILED,
    RuntimeStatus.CANCELLED,
    RuntimeStatus.TIMED_OUT,
    RuntimeStatus.LOST,
})

#: The only non-terminal statuses a run may be in.
ACTIVE_RUNTIME_STATUSES = frozenset(set(RuntimeStatus) - TERMINAL_RUNTIME_STATUSES)


def is_terminal(status: RuntimeStatus) -> bool:
    return status in TERMINAL_RUNTIME_STATUSES


class AttemptStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    LOST = "lost"


#: Legal attempt transitions.  Runtime enforces these; an illegal transition is
#: an error, never a silent correction.
ATTEMPT_TRANSITIONS: dict[AttemptStatus, frozenset[AttemptStatus]] = {
    AttemptStatus.PENDING: frozenset({AttemptStatus.RUNNING, AttemptStatus.LOST}),
    AttemptStatus.RUNNING: frozenset({
        AttemptStatus.SUCCEEDED, AttemptStatus.FAILED, AttemptStatus.LOST,
    }),
    AttemptStatus.SUCCEEDED: frozenset(),
    AttemptStatus.FAILED: frozenset(),
    AttemptStatus.LOST: frozenset(),
}


def attempt_transition_allowed(current: AttemptStatus, target: AttemptStatus) -> bool:
    return target in ATTEMPT_TRANSITIONS[current]


#: Run transitions Runtime is allowed to make.  Encoded as data so the rule is
#: testable and so the API layer cannot invent its own state machine.
RUN_TRANSITIONS: dict[RuntimeStatus, frozenset[RuntimeStatus]] = {
    RuntimeStatus.QUEUED: frozenset({
        RuntimeStatus.PREPARING, RuntimeStatus.CANCEL_REQUESTED,
        RuntimeStatus.CANCELLED, RuntimeStatus.FAILED,
    }),
    RuntimeStatus.PREPARING: frozenset({
        RuntimeStatus.RUNNING, RuntimeStatus.CANCEL_REQUESTED,
        RuntimeStatus.FAILED, RuntimeStatus.LOST,
    }),
    RuntimeStatus.RUNNING: frozenset({
        RuntimeStatus.RETRY_WAIT, RuntimeStatus.CANCEL_REQUESTED,
        RuntimeStatus.SUCCEEDED, RuntimeStatus.FAILED,
        RuntimeStatus.TIMED_OUT, RuntimeStatus.LOST,
    }),
    RuntimeStatus.RETRY_WAIT: frozenset({
        RuntimeStatus.QUEUED, RuntimeStatus.CANCEL_REQUESTED,
        RuntimeStatus.FAILED, RuntimeStatus.LOST,
    }),
    RuntimeStatus.CANCEL_REQUESTED: frozenset({
        RuntimeStatus.CANCELLED, RuntimeStatus.FAILED, RuntimeStatus.LOST,
    }),
}


def run_transition_allowed(current: RuntimeStatus, target: RuntimeStatus) -> bool:
    if current in TERMINAL_RUNTIME_STATUSES:
        return False
    return target in RUN_TRANSITIONS.get(current, frozenset())


@dataclass(frozen=True)
class Failure:
    """A classified failure.  A failure is evidence, never something to hide."""

    category: str
    message: str
    retryable: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        validate_identifier("category", self.category)
        if not isinstance(self.message, str) or not self.message:
            raise ContractError("failure message is required")
        validate_mapping("detail", self.detail)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return primitive(self)


@dataclass(frozen=True)
class Event:
    """An append-only fact about a run.

    ``producer`` names who emitted it.  A view may be derived from events;
    an event is never derived from a view.
    """

    event_id: str
    run_id: str
    event_type: str
    occurred_at: str
    producer: str
    payload: dict[str, Any] = field(default_factory=dict)
    stage_run_id: str | None = None
    attempt_id: str | None = None
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        validate_version(self.schema_version)
        for name in ("event_id", "run_id", "event_type", "producer"):
            validate_identifier(name, getattr(self, name))
        validate_identifier("stage_run_id", self.stage_run_id, required=False)
        validate_identifier("attempt_id", self.attempt_id, required=False)
        if not isinstance(self.occurred_at, str) or not self.occurred_at:
            raise ContractError("occurred_at is required")
        validate_mapping("payload", self.payload)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Event":
        result = instantiate(cls, known_payload(cls, payload))
        result.validate()
        return result
