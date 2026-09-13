"""The progress envelope: how a plugin reports stages without the kernel
knowing any stage names.

The previous runtime carried a regular expression listing six hardcoded stage
names belonging to one vendor's tool, together with that vendor's marker
prefix.  That is a control plane which has memorised one tool's internals: it
cannot host a second tool without being edited, and editing it is how vendor
knowledge accumulates in a layer that is supposed to have none.

The fix is not a better regular expression.  It is to move the vocabulary into
a contract.  An adapter emits one JSON object per line, prefixed by the marker
its manifest declares:

    [progress] {"stage": "<any-label>", "phase": "started"}
    [progress] {"stage": "<any-label>", "phase": "finished",
                "status": "succeeded", "seconds": 12.5}

The kernel understands the *envelope* and nothing else.  The stage label is
opaque data that travels to the event store and out to whoever renders it.  A
plugin running a completely different toolchain uses the same contract, and no
kernel file needs to change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .version import (
    ContractError,
    SCHEMA_VERSION,
    known_payload,
    primitive,
    validate_mapping,
    validate_version,
)

#: Default marker.  A manifest may override it; the kernel never hardcodes a
#: tool-specific prefix.
DEFAULT_PROGRESS_MARKER = "[progress]"

#: Hard ceiling on one envelope line, so a malformed adapter cannot make the
#: kernel buffer an unbounded string.
MAX_ENVELOPE_BYTES = 4096


class ProgressPhase(str, Enum):
    STARTED = "started"
    FINISHED = "finished"


class ProgressStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ProgressReport:
    """One decoded progress envelope.

    ``stage`` is free-form and opaque to the kernel.  It is validated only for
    shape: non-empty, bounded, and free of control characters, because it ends
    up in an event payload and eventually on someone's screen.
    """

    stage: str
    phase: ProgressPhase
    status: ProgressStatus | None = None
    seconds: float | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    #: Bound on the opaque stage label.  Generous for real tools, tight enough
    #: that a runaway adapter cannot inject a novel into the event store.
    MAX_STAGE_LENGTH = 64

    def validate(self) -> None:
        validate_version(self.schema_version)
        if not isinstance(self.stage, str) or not self.stage:
            raise ContractError("progress stage is required")
        if len(self.stage) > self.MAX_STAGE_LENGTH:
            raise ContractError(
                f"progress stage exceeds {self.MAX_STAGE_LENGTH} characters"
            )
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in self.stage):
            raise ContractError("progress stage contains control characters")
        if self.phase is ProgressPhase.FINISHED and self.status is None:
            raise ContractError("a finished report requires a status")
        if self.phase is ProgressPhase.STARTED and self.status is not None:
            raise ContractError("a started report must not carry a status")
        if self.seconds is not None and (
            not isinstance(self.seconds, (int, float)) or self.seconds < 0
        ):
            raise ContractError("seconds must be a non-negative number")
        validate_mapping("detail", self.detail)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProgressReport":
        value = known_payload(cls, payload)
        for key, enum in (("phase", ProgressPhase), ("status", ProgressStatus)):
            if key in value and value[key] is not None:
                try:
                    value[key] = enum(value[key])
                except ValueError as exc:
                    raise ContractError(f"invalid progress {key}") from exc
        value["detail"] = dict(value.get("detail") or {})
        result = cls(**value)
        result.validate()
        return result


def decode_progress_line(line: str, marker: str = DEFAULT_PROGRESS_MARKER) -> ProgressReport | None:
    """Decode one output line, or return ``None`` if it is not an envelope.

    Returning ``None`` for ordinary tool output is deliberate: the overwhelming
    majority of an EDA log is not a progress report, and raising on it would
    make the observer unusable.

    The envelope is a log line, so it does not repeat ``schema_version`` on
    every record; the version is the platform's, supplied here.
    """
    stripped = line.strip()
    if not stripped.startswith(marker):
        return None
    payload_text = stripped[len(marker):].strip()
    if not payload_text or len(payload_text) > MAX_ENVELOPE_BYTES:
        raise ContractError("progress envelope payload is empty or oversized")
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise ContractError(f"progress envelope is not valid JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ContractError("progress envelope must be a JSON object")
    return ProgressReport.from_dict({"schema_version": SCHEMA_VERSION, **payload})
