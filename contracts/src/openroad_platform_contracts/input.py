"""Inputs the platform places, and the identity they are given.

A task may declare the files it needs.  The platform copies each one into the
attempt workspace, measures it from the bytes on disk, and records the
measurement against that attempt.

The reason this exists at all: ``design_id`` is a *label* the caller chooses, and
two runs carrying the same label may have read different bytes.  Every platform
number derived from such a comparison is then unfalsifiable.  A digest is not a
label -- it is an identity, and it is the one the platform measured rather than
the one it was told.

This mirrors artifacts exactly.  An artifact is what came out of an attempt and
is measured by the platform; an input is what went in, measured the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .version import (
    ContractError,
    instantiate,
    validate_relative_path,
    validate_sha256,
)

#: Artifact kind the platform reserves for the manifest it writes.  Registering
#: it makes the input set a first-class piece of evidence: one digest covering
#: every input at once, which is what "the same design" reduces to.
INPUT_MANIFEST_KIND = "runtime_input_manifest"

#: The manifest's name inside the attempt workspace.  Part of the protocol, not
#: an implementation detail: an adapter reads it to cite the digest it was given.
INPUT_MANIFEST_FILENAME = "input_manifest.json"


@dataclass(frozen=True)
class InputFile:
    """One file the platform must place in the attempt workspace.

    ``source`` is an absolute host path and must be, deliberately.  A relative
    source would be resolved against whichever worker happened to pick the run
    up, so the same task could stage different bytes on two workers while every
    record insisted it was the same task.

    ``destination`` is where the adapter will find it, relative to the attempt
    workspace.  The platform chooses the placement; the plugin never hunts for
    a path.
    """

    source: str
    destination: str
    #: A missing optional input is recorded as absent, not as an error.  A
    #: capability that can do useful work without a file says so here rather
    #: than having the caller guess whether it will be needed.
    required: bool = True

    def validate(self) -> None:
        if not isinstance(self.source, str) or not self.source:
            raise ContractError("input source is required")
        if not self.source.startswith("/"):
            raise ContractError(
                f"input source must be an absolute path: {self.source!r}"
            )
        validate_relative_path(
            "input destination", self.destination, container="attempt workspace"
        )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "source": self.source,
            "destination": self.destination,
            "required": self.required,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "InputFile":
        allowed = {"source", "destination", "required"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ContractError(
                f"unknown InputFile fields: {', '.join(unknown)}"
            )
        missing = sorted(allowed - set(payload) - {"required"})
        if missing:
            raise ContractError(
                f"InputFile is missing: {', '.join(missing)}"
            )
        result = instantiate(cls, dict(payload))
        result.validate()
        return result


@dataclass(frozen=True)
class StagedInput:
    """What the platform measured when it placed one input.

    Produced by the platform, never submitted by a plugin or an app.  A caller
    that could write this record could claim a digest for bytes it never read,
    which is the whole thing digesting is supposed to prevent.
    """

    destination: str
    source: str
    present: bool
    size_bytes: int
    #: ``None`` only when ``present`` is false: an optional input that was not
    #: there has no digest, and inventing one (say, of the empty string) would
    #: make two different absences look like the same file.
    sha256: str | None = None

    def validate(self) -> None:
        validate_relative_path(
            "input destination", self.destination, container="attempt workspace"
        )
        if not isinstance(self.source, str) or not self.source:
            raise ContractError("input source is required")
        if not isinstance(self.present, bool):
            raise ContractError("input present must be a boolean")
        if not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise ContractError("input size_bytes must be a non-negative integer")
        if self.present:
            if self.sha256 is None:
                raise ContractError("a staged input that is present needs a sha256")
            validate_sha256("input sha256", self.sha256)
        elif self.sha256 is not None:
            raise ContractError(
                "a staged input that is absent may not carry a sha256"
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "destination": self.destination,
            "source": self.source,
            "present": self.present,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StagedInput":
        allowed = {"destination", "source", "present", "size_bytes", "sha256"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ContractError(
                f"unknown StagedInput fields: {', '.join(unknown)}"
            )
        result = instantiate(cls, dict(payload))
        result.validate()
        return result


__all__ = (
    "INPUT_MANIFEST_FILENAME",
    "INPUT_MANIFEST_KIND",
    "InputFile",
    "StagedInput",
)
