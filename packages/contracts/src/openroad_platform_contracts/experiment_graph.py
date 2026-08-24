"""Contracts for the v2 experiment graph.

The graph is intentionally an audit model, not an orchestration API.  An agent
may create a proposal node, but only a reviewed ActionSpec can be submitted to
the Workflow Runtime by an application service.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .platform import IDENTIFIER, SCHEMA_VERSION, _known_payload, _primitive, _validate_mapping, _validate_version


class ExperimentNodeKind(str, Enum):
    DESIGN_REVISION = "design_revision"
    BASELINE = "baseline"
    OBSERVATION = "observation"
    DIAGNOSIS = "diagnosis"
    PROPOSAL = "proposal"
    REVIEW = "review"
    ATTEMPT = "attempt"
    MEASUREMENT = "measurement"
    DECISION = "decision"
    MEMORY = "memory"


class ActionKind(str, Enum):
    PARAMETER = "parameter"
    REPAIR = "repair"
    RTL_CANDIDATE = "rtl_candidate"
    TOOL_CODE = "tool_code"


@dataclass(frozen=True)
class ExperimentNode:
    """Immutable, provenance-bearing unit in an Experiment Graph."""

    node_id: str
    experiment_id: str
    kind: ExperimentNodeKind
    producer: str
    payload: dict[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    created_at: str = ""
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        _validate_version(self.schema_version)
        for name, value in (("node_id", self.node_id),
                            ("experiment_id", self.experiment_id),
                            ("producer", self.producer)):
            if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
                raise ValueError(f"Invalid {name}: {value!r}")
        if not isinstance(self.kind, ExperimentNodeKind):
            raise ValueError("kind must be an ExperimentNodeKind")
        _validate_mapping("payload", self.payload)
        if not self.created_at:
            raise ValueError("created_at is required")
        if not all(isinstance(item, str) and item for item in self.evidence_refs):
            raise ValueError("evidence_refs must contain non-empty strings")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExperimentNode":
        value = _known_payload(cls, payload)
        value["kind"] = ExperimentNodeKind(value["kind"])
        value["evidence_refs"] = tuple(value.get("evidence_refs", ()))
        result = cls(**value)
        result.validate()
        return result


@dataclass(frozen=True)
class ExperimentEdge:
    """A directed, typed relation. Edges never mutate either endpoint."""

    experiment_id: str
    parent_node_id: str
    child_node_id: str
    relation: str
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        _validate_version(self.schema_version)
        for name, value in (("experiment_id", self.experiment_id),
                            ("parent_node_id", self.parent_node_id),
                            ("child_node_id", self.child_node_id),
                            ("relation", self.relation)):
            if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
                raise ValueError(f"Invalid {name}: {value!r}")
        if self.parent_node_id == self.child_node_id:
            raise ValueError("ExperimentEdge cannot self-reference")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExperimentEdge":
        result = cls(**_known_payload(cls, payload))
        result.validate()
        return result


@dataclass(frozen=True)
class ActionSpec:
    """A reviewed and bounded action eligible for runtime submission.

    ``ActionSpec`` contains no command, shell text, path or credential.  Tool
    code changes are represented only by an approved patch artifact reference.
    """

    action_id: str
    experiment_id: str
    proposal_node_id: str
    kind: ActionKind
    hypothesis: str
    expected_outcome: str
    stop_condition: str
    rollback: str
    parameters: dict[str, Any]
    evidence_refs: tuple[str, ...]
    reviewed_by: str
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        _validate_version(self.schema_version)
        for name, value in (("action_id", self.action_id),
                            ("experiment_id", self.experiment_id),
                            ("proposal_node_id", self.proposal_node_id),
                            ("reviewed_by", self.reviewed_by)):
            if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
                raise ValueError(f"Invalid {name}: {value!r}")
        if not isinstance(self.kind, ActionKind):
            raise ValueError("kind must be an ActionKind")
        for name, value in (("hypothesis", self.hypothesis),
                            ("expected_outcome", self.expected_outcome),
                            ("stop_condition", self.stop_condition),
                            ("rollback", self.rollback)):
            if not isinstance(value, str) or not value.strip() or len(value) > 4000:
                raise ValueError(f"{name} must be non-empty text up to 4000 characters")
        _validate_mapping("parameters", self.parameters)
        if not self.evidence_refs or not all(isinstance(item, str) and item for item in self.evidence_refs):
            raise ValueError("ActionSpec requires evidence_refs")
        self._validate_template()

    def _validate_template(self) -> None:
        forbidden = {"command", "shell", "script", "path", "credential", "api_key"}
        if forbidden & {key.lower() for key in self.parameters}:
            raise ValueError("ActionSpec parameters cannot contain executable or secret fields")
        if self.kind is ActionKind.PARAMETER:
            if set(self.parameters) != {"values"} or not isinstance(self.parameters["values"], Mapping):
                raise ValueError("parameter ActionSpec requires only a values object")
        elif self.kind is ActionKind.REPAIR:
            if set(self.parameters) != {"repair_action"} or not isinstance(self.parameters["repair_action"], Mapping):
                raise ValueError("repair ActionSpec requires a repair_action object")
        elif self.kind is ActionKind.RTL_CANDIDATE:
            if set(self.parameters) != {"candidate_ref"} or not isinstance(self.parameters["candidate_ref"], str):
                raise ValueError("rtl_candidate ActionSpec requires a candidate_ref")
        elif self.kind is ActionKind.TOOL_CODE:
            if set(self.parameters) != {"patch_ref", "patch_surface"}:
                raise ValueError("tool_code ActionSpec requires patch_ref and patch_surface")
            if not all(isinstance(self.parameters[key], str) and self.parameters[key]
                       for key in ("patch_ref", "patch_surface")):
                raise ValueError("tool_code patch fields must be non-empty strings")
            if not self.parameters["patch_ref"].startswith("artifact:patch-registry:"):
                raise ValueError("tool_code patch_ref must be an immutable patch-registry artifact")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ActionSpec":
        value = _known_payload(cls, payload)
        value["kind"] = ActionKind(value["kind"])
        value["evidence_refs"] = tuple(value.get("evidence_refs", ()))
        result = cls(**value)
        result.validate()
        return result
