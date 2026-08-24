"""Versioned contracts for Spec-to-Verified-RTL.

These contracts separate design intent, verification oracle and generated RTL.
That separation prevents an LLM-produced source string from being treated as a
specification or a correctness result.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from .platform import IDENTIFIER, SCHEMA_VERSION, _known_payload, _primitive, _validate_mapping, _validate_version


def _id(name: str, value: str) -> None:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid {name}: {value!r}")


def _text(name: str, value: str, maximum: int = 8000) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be non-empty text up to {maximum} characters")


@dataclass(frozen=True)
class PortSpec:
    name: str
    direction: str
    # ``None`` is an explicit unknown, never an implicit claim of one bit.
    width: int | None = None
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        _validate_version(self.schema_version)
        _id("port name", self.name)
        if self.direction not in {"input", "output", "inout"}:
            raise ValueError("PortSpec direction must be input, output, or inout")
        if self.width is not None and (not isinstance(self.width, int) or not 1 <= self.width <= 65536):
            raise ValueError("PortSpec width must be None or an integer in [1, 65536]")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PortSpec":
        result = cls(**_known_payload(cls, payload))
        result.validate()
        return result


@dataclass(frozen=True)
class SpecIR:
    """Executable design intent, deliberately without generated RTL."""

    spec_id: str
    design_id: str
    top: str
    functionality: str
    objective: str
    ports: tuple[PortSpec, ...]
    clock: str | None = None
    reset: str | None = None
    constraints: dict[str, Any] = field(default_factory=dict)
    assumptions: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        _validate_version(self.schema_version)
        for name, value in (("spec_id", self.spec_id), ("design_id", self.design_id), ("top", self.top)):
            _id(name, value)
        _text("functionality", self.functionality)
        _text("objective", self.objective, maximum=2000)
        if not self.ports:
            raise ValueError("SpecIR requires at least one port")
        names = set()
        for port in self.ports:
            port.validate()
            if port.name in names:
                raise ValueError("SpecIR port names must be unique")
            names.add(port.name)
        for name, value in (("clock", self.clock), ("reset", self.reset)):
            if value is not None:
                _id(name, value)
                if value not in names:
                    raise ValueError(f"SpecIR {name} must name a declared port")
        _validate_mapping("constraints", self.constraints)
        if not self.acceptance_criteria:
            raise ValueError("SpecIR requires acceptance_criteria")
        for item in (*self.assumptions, *self.acceptance_criteria):
            _text("SpecIR list item", item, maximum=2000)

    @property
    def fingerprint(self) -> str:
        self.validate()
        return hashlib.sha256(json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":")).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SpecIR":
        value = _known_payload(cls, payload)
        value["ports"] = tuple(PortSpec.from_dict(item) for item in value.get("ports", ()))
        value["assumptions"] = tuple(value.get("assumptions", ()))
        value["acceptance_criteria"] = tuple(value.get("acceptance_criteria", ()))
        result = cls(**value)
        result.validate()
        return result


@dataclass(frozen=True)
class VerificationPackage:
    """Pointers to frozen checks; the candidate does not author its own oracle."""

    verification_id: str
    spec_id: str
    compile_checks: tuple[str, ...]
    simulation_oracle_refs: tuple[str, ...] = ()
    formal_property_refs: tuple[str, ...] = ()
    coverage_targets: dict[str, float] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        _validate_version(self.schema_version)
        _id("verification_id", self.verification_id)
        _id("spec_id", self.spec_id)
        if not self.compile_checks or not all(isinstance(item, str) and item for item in self.compile_checks):
            raise ValueError("VerificationPackage requires compile_checks")
        for group in (self.simulation_oracle_refs, self.formal_property_refs):
            if not all(isinstance(item, str) and item.startswith(("artifact:", "source:")) for item in group):
                raise ValueError("Verification oracle references must be durable artifact/source refs")
        _validate_mapping("coverage_targets", self.coverage_targets)
        for key, value in self.coverage_targets.items():
            if not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                raise ValueError("coverage_targets must be named fractions in [0, 1]")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "VerificationPackage":
        value = _known_payload(cls, payload)
        for name in ("compile_checks", "simulation_oracle_refs", "formal_property_refs"):
            value[name] = tuple(value.get(name, ()))
        result = cls(**value)
        result.validate()
        return result


@dataclass(frozen=True)
class RTLCandidate:
    candidate_id: str
    spec_id: str
    verification_id: str
    rtl_artifact_ref: str
    generator: str
    parent_candidate_ids: tuple[str, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        _validate_version(self.schema_version)
        for name, value in (("candidate_id", self.candidate_id), ("spec_id", self.spec_id),
                            ("verification_id", self.verification_id), ("generator", self.generator)):
            _id(name, value)
        if not isinstance(self.rtl_artifact_ref, str) or not self.rtl_artifact_ref.startswith("artifact:"):
            raise ValueError("RTLCandidate requires a durable RTL artifact reference")
        for parent in self.parent_candidate_ids:
            _id("parent_candidate_id", parent)
            if parent == self.candidate_id:
                raise ValueError("RTLCandidate cannot be its own parent")
        _validate_mapping("provenance", self.provenance)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RTLCandidate":
        value = _known_payload(cls, payload)
        value["parent_candidate_ids"] = tuple(value.get("parent_candidate_ids", ()))
        result = cls(**value)
        result.validate()
        return result
