"""Artifacts, metrics, and the rules that decide whether a claim is admissible.

An artifact is a file with a hash.  A metric is a number that points at the
artifact it came from.  A metric with no artifact behind it is an assertion,
and the platform does not treat assertions as evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .input import INPUT_MANIFEST_KIND
from .version import (
    SCHEMA_VERSION,
    ContractError,
    instantiate,
    primitive,
    validate_identifier,
    validate_mapping,
    validate_relative_path,
    validate_sha256,
)

#: Artifact kinds reserved by the kernel.  An adapter that declares one of
#: these is trying to forge platform authority, so the kernel rejects it.
RESERVED_ARTIFACT_KINDS = frozenset({
    "runtime_protocol_receipt",
    "protected_evaluation",
    INPUT_MANIFEST_KIND,
})

#: Metric context keys the kernel owns.  A plugin may not set these.
RESERVED_METRIC_CONTEXT_KEYS = frozenset({
    "runtime_authority",
    "official_qor",
})


@dataclass(frozen=True)
class ArtifactDeclaration:
    """What an adapter says it produced, relative to its attempt workspace."""

    kind: str
    path: str
    required: bool = True
    media_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    #: Only the platform sets this.  An adapter that could register a
    #: reserved kind could forge the platform's own bookkeeping.
    allow_reserved: bool = False

    def validate(self) -> None:
        validate_identifier("kind", self.kind)
        if self.kind in RESERVED_ARTIFACT_KINDS and not self.allow_reserved:
            raise ContractError(
                f"artifact kind {self.kind!r} is reserved by the platform"
            )
        if not isinstance(self.path, str) or not self.path:
            raise ContractError("artifact path is required")
        validate_relative_path(
            "artifact path", self.path, container="attempt workspace"
        )
        if self.media_type is not None and not isinstance(self.media_type, str):
            raise ContractError("media_type must be a string")
        validate_mapping("metadata", self.metadata)
        authority = self.metadata.get("runtime_authority")
        if authority is not None:
            raise ContractError(
                "an adapter may not declare runtime_authority; "
                "only the kernel sets it"
            )


@dataclass(frozen=True)
class Artifact:
    """A registered, hash-verified artifact."""

    artifact_id: str
    kind: str
    store_key: str
    sha256: str
    size_bytes: int
    media_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        validate_identifier("artifact_id", self.artifact_id)
        validate_identifier("kind", self.kind)
        if not isinstance(self.store_key, str) or not self.store_key:
            raise ContractError("store_key is required")
        validate_relative_path(
            "store_key", self.store_key, container="artifact store"
        )
        validate_sha256("sha256", self.sha256)
        if not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise ContractError("size_bytes must be a non-negative integer")
        validate_mapping("metadata", self.metadata)


@dataclass(frozen=True)
class Metric:
    """One measured number, tied to the artifact it was read from.

    ``source_artifact_id`` is what makes a metric auditable.  A metric without
    it is a display value, not evidence, and the evaluator refuses to build a
    verdict from one.
    """

    name: str
    value: float | int | bool | str
    unit: str | None = None
    source_artifact_id: str | None = None
    parser_id: str | None = None
    parser_version: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        validate_identifier("name", self.name)
        if not isinstance(self.value, (int, float, bool, str)):
            raise ContractError("metric value must be a JSON scalar")
        if isinstance(self.value, float) and self.value != self.value:
            raise ContractError("metric value must not be NaN")
        if self.unit is not None:
            validate_identifier("unit", self.unit)
        validate_identifier("source_artifact_id", self.source_artifact_id, required=False)
        validate_identifier("parser_id", self.parser_id, required=False)
        validate_identifier("parser_version", self.parser_version, required=False)
        validate_mapping("context", self.context)
        leaked = RESERVED_METRIC_CONTEXT_KEYS & set(self.context)
        if leaked:
            raise ContractError(
                f"metric context may not set kernel-reserved keys: {sorted(leaked)}"
            )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Metric:
        from .version import known_payload
        value = known_payload(cls, payload)
        value["context"] = dict(value.get("context") or {})
        result = instantiate(cls, value)
        result.validate()
        return result
