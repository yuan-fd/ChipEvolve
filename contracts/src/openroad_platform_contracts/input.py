"""Contracts for files staged into an attempt workspace."""

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

    The bytes come from exactly one of three places, and the contract refuses a
    task that tries to name both or neither:

    * ``source`` -- an absolute host path.  Absolute deliberately: a relative
      source would be resolved against whichever worker happened to pick the run
      up, so the same task could stage different bytes on two workers while
      every record insisted it was the same task.
    * ``artifact_id`` -- bytes the platform has already registered.  This is how
      one run consumes what another produced without either of them knowing a
      filesystem path, and the digest is checked as the bytes are placed.
    * ``input_id`` -- bytes uploaded to the platform before task submission.
      This lets a remote Agent provide generated scripts or patches without
      exposing a server-local path.

    ``destination`` is where the adapter will find it, relative to the attempt
    workspace.  The platform chooses the placement; the plugin never hunts for
    a path.
    """

    destination: str
    source: str | None = None
    artifact_id: str | None = None
    input_id: str | None = None
    #: A missing optional input is recorded as absent, not as an error.  A
    #: capability that can do useful work without a file says so here rather
    #: than having the caller guess whether it will be needed.
    required: bool = True

    def validate(self) -> None:
        validate_relative_path(
            "input destination", self.destination, container="attempt workspace"
        )
        locations = (self.source, self.artifact_id, self.input_id)
        if sum(location is not None for location in locations) != 1:
            raise ContractError(
                "an input names exactly one place its bytes come from: source "
                "(a host path), artifact_id (a run artifact), or input_id "
                "(an uploaded input)"
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
        if self.input_id is not None:
            validate_identifier("input input_id", self.input_id)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "destination": self.destination,
            "source": self.source,
            "artifact_id": self.artifact_id,
            "input_id": self.input_id,
            "required": self.required,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> InputFile:
        allowed = {"destination", "source", "artifact_id", "input_id", "required"}
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
    """Measured bytes placed into an attempt workspace."""

    destination: str
    present: bool
    size_bytes: int
    #: ``None`` only when ``present`` is false: an optional input that was not
    #: there has no digest, and inventing one (say, of the empty string) would
    #: make two different absences look like the same file.
    sha256: str | None = None
    source: str | None = None
    source_artifact_id: str | None = None
    source_input_id: str | None = None

    def validate(self) -> None:
        validate_relative_path(
            "input destination", self.destination, container="attempt workspace"
        )
        sources = (self.source, self.source_artifact_id, self.source_input_id)
        if sum(source is not None for source in sources) != 1:
            raise ContractError(
                "a staged input names exactly one place its bytes came from"
            )
        if self.source_input_id is not None:
            validate_identifier("staged source_input_id", self.source_input_id)
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
            "source_input_id": self.source_input_id,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> StagedInput:
        allowed = {"destination", "present", "size_bytes", "sha256", "source",
                   "source_artifact_id", "source_input_id"}
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
