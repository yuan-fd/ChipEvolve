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
Which is why an input may also be *sourced from* an artifact: the platform
already holds those bytes, under a name that is their digest, and re-reading
them from a path on a host would be a worse way to get the same file.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .version import (
    ContractError,
    instantiate,
    validate_identifier,
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

    The bytes come from exactly one of two places, and the contract refuses a
    task that tries to name both or neither:

    * ``source`` -- an absolute host path.  Absolute deliberately: a relative
      source would be resolved against whichever worker happened to pick the run
      up, so the same task could stage different bytes on two workers while
      every record insisted it was the same task.
    * ``artifact_id`` -- bytes the platform has already registered.  This is how
      one run consumes what another produced without either of them knowing a
      filesystem path, and the digest is checked as the bytes are placed.

    ``destination`` is where the adapter will find it, relative to the attempt
    workspace.  The platform chooses the placement; the plugin never hunts for
    a path.
    """

    destination: str
    source: str | None = None
    artifact_id: str | None = None
    #: A missing optional input is recorded as absent, not as an error.  A
    #: capability that can do useful work without a file says so here rather
    #: than having the caller guess whether it will be needed.
    required: bool = True

    def validate(self) -> None:
        validate_relative_path(
            "input destination", self.destination, container="attempt workspace"
        )
        if (self.source is None) == (self.artifact_id is None):
            raise ContractError(
                "an input names exactly one place its bytes come from: "
                "source (a host path) or artifact_id (bytes the platform "
                "already holds)"
            )
        if self.source is not None:
            if not isinstance(self.source, str) or not self.source:
                raise ContractError("input source is required")
            if not self.source.startswith("/"):
                raise ContractError(
                    f"input source must be an absolute path: {self.source!r}"
                )
        if self.artifact_id is not None:
            validate_identifier("input artifact_id", self.artifact_id)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "destination": self.destination,
            "source": self.source,
            "artifact_id": self.artifact_id,
            "required": self.required,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> InputFile:
        allowed = {"destination", "source", "artifact_id", "required"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ContractError(f"unknown InputFile fields: {', '.join(unknown)}")
        if "destination" not in payload:
            raise ContractError("InputFile is missing: destination")
        result = instantiate(cls, dict(payload))
        result.validate()
        return result


@dataclass(frozen=True)
class StagedInput:
    """What the platform measured when it placed one input.

    Produced by the platform, never submitted by a plugin or an app.  A caller
    that could write this record could claim a digest for bytes it never read,
    which is the whole thing digesting is supposed to prevent.

    ``source`` and ``source_artifact_id`` say where the bytes came from, for a
    reader asking later.  Exactly one is set.  Neither is part of the input's
    *identity* -- see the manifest, which omits them on purpose.
    """

    destination: str
    present: bool
    size_bytes: int
    #: ``None`` only when ``present`` is false: an optional input that was not
    #: there has no digest, and inventing one (say, of the empty string) would
    #: make two different absences look like the same file.
    sha256: str | None = None
    source: str | None = None
    source_artifact_id: str | None = None

    def validate(self) -> None:
        validate_relative_path(
            "input destination", self.destination, container="attempt workspace"
        )
        if (self.source is None) == (self.source_artifact_id is None):
            raise ContractError(
                "a staged input names exactly one place its bytes came from"
            )
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
            "present": self.present,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "source": self.source,
            "source_artifact_id": self.source_artifact_id,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> StagedInput:
        allowed = {"destination", "present", "size_bytes", "sha256", "source",
                   "source_artifact_id"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ContractError(f"unknown StagedInput fields: {', '.join(unknown)}")
        for name in ("destination", "present", "size_bytes"):
            if name not in payload:
                raise ContractError(f"StagedInput is missing: {name}")
        result = instantiate(cls, dict(payload))
        result.validate()
        return result


__all__ = (
    "INPUT_MANIFEST_FILENAME",
    "INPUT_MANIFEST_KIND",
    "InputFile",
    "StagedInput",
)
