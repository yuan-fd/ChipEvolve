"""Schema versioning and the shared validation primitives.

Every public contract carries ``schema_version`` and refuses to load a payload
it does not understand.  A contract that silently accepts unknown fields is how
two versions of a platform end up disagreeing about the same artifact.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

#: Bumped when a contract changes shape.  A consumer must reject anything it
#: was not compiled against rather than guess.
SCHEMA_VERSION = 3

#: Identifiers that may appear in a task, run, plugin, or event.
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

#: SHA-256 digests are the platform's only notion of artifact identity.
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class ContractError(ValueError):
    """A payload does not satisfy its contract.

    Raised loudly and specifically.  Never caught to fall back to a guess:
    a fallback here would turn a schema error into silent data corruption.
    """


def validate_version(value: int) -> None:
    if value != SCHEMA_VERSION:
        raise ContractError(
            f"unsupported schema_version {value!r}; this build speaks "
            f"version {SCHEMA_VERSION}"
        )


def validate_identifier(name: str, value: str | None, *, required: bool = True) -> None:
    if value is None and not required:
        return
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ContractError(f"invalid {name}: {value!r}")


def validate_mapping(name: str, value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or not all(isinstance(k, str) for k in value):
        raise ContractError(f"{name} must be a string-keyed object")


def validate_sha256(name: str, value: str) -> None:
    if not isinstance(value, str) or not SHA256_HEX.fullmatch(value):
        raise ContractError(f"{name} must be a lowercase sha256 hex digest")


def primitive(value: Any) -> Any:
    """Convert a contract graph into JSON-serialisable primitives."""
    if dataclasses.is_dataclass(value):
        return {
            f.name: primitive(getattr(value, f.name))
            for f in dataclasses.fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): primitive(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [primitive(item) for item in value]
    return value


def instantiate(cls: type, value: Mapping[str, Any]):
    """Construct a contract, reporting a malformed payload as such.

    A missing or unexpected field is the caller's error, not the server's.
    Letting the constructor raise ``TypeError`` turns a 400 into a 500 and
    tells the caller nothing about what was wrong.
    """
    try:
        return cls(**value)
    except TypeError as exc:
        raise ContractError(
            f"{cls.__name__} payload does not match its fields: {exc}"
        ) from exc


def known_payload(cls: type, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Reject unknown fields instead of ignoring them.

    Silently dropping an unknown key means a newer producer's intent is lost
    with no error, which is exactly how a "compatible" change becomes a wrong
    result.
    """
    if not isinstance(payload, Mapping):
        raise ContractError(f"{cls.__name__} payload must be an object")
    if "schema_version" not in payload:
        raise ContractError(f"{cls.__name__} requires schema_version")
    allowed = {f.name for f in dataclasses.fields(cls)}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ContractError(f"unknown {cls.__name__} fields: {', '.join(unknown)}")
    return dict(payload)


@dataclass(frozen=True)
class Contract:
    """Base class for versioned, round-trippable contracts."""

    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:  # pragma: no cover - overridden
        validate_version(self.schema_version)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]):
        value = known_payload(cls, payload)
        result = instantiate(cls, value)
        result.validate()
        return result
