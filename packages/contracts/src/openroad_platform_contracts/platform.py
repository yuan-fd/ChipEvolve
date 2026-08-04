"""Versioned contracts for the generic workflow control plane."""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


SCHEMA_VERSION = 1
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


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


TERMINAL_RUNTIME_STATUSES = {
    RuntimeStatus.SUCCEEDED,
    RuntimeStatus.FAILED,
    RuntimeStatus.CANCELLED,
    RuntimeStatus.TIMED_OUT,
    RuntimeStatus.LOST,
}


def _validate_version(value: int) -> None:
    if value != SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported schema_version {value!r}; expected {SCHEMA_VERSION}"
        )


def _validate_identifier(name: str, value: str | None, *, required: bool = True) -> None:
    if value is None and not required:
        return
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid {name}: {value!r}")


def _validate_mapping(name: str, value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be a string-keyed object")


def _primitive(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {
            item.name: _primitive(getattr(value, item.name))
            for item in dataclasses.fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_primitive(item) for item in value]
    return value


def _known_payload(cls: type, payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{cls.__name__} payload must be an object")
    if "schema_version" not in payload:
        raise ValueError(f"{cls.__name__} requires schema_version")
    allowed = {item.name for item in dataclasses.fields(cls)}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"Unknown {cls.__name__} fields: {', '.join(unknown)}")
    return dict(payload)


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    project_id: str
    design_id: str
    plugin_id: str | None = None
    workflow_id: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)
    resources: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: int = 3600
    max_attempts: int = 1
    expected_artifacts: tuple[str, ...] = ()
    labels: dict[str, str] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        _validate_version(self.schema_version)
        for name, value in (
            ("task_id", self.task_id),
            ("project_id", self.project_id),
            ("design_id", self.design_id),
        ):
            _validate_identifier(name, value)
        if (self.plugin_id is None) == (self.workflow_id is None):
            raise ValueError("TaskSpec must define exactly one of plugin_id or workflow_id")
        _validate_identifier("plugin_id", self.plugin_id, required=False)
        _validate_identifier("workflow_id", self.workflow_id, required=False)
        for name, value in (
            ("inputs", self.inputs),
            ("parameters", self.parameters),
            ("resources", self.resources),
            ("labels", self.labels),
        ):
            _validate_mapping(name, value)
        if not isinstance(self.timeout_seconds, int) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive integer")
        if not isinstance(self.max_attempts, int) or self.max_attempts <= 0:
            raise ValueError("max_attempts must be a positive integer")
        if not all(IDENTIFIER.fullmatch(item) for item in self.expected_artifacts):
            raise ValueError("expected_artifacts contains an invalid kind")

    def to_dict(self) -> dict[str, Any]:
        return _primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TaskSpec":
        value = _known_payload(cls, payload)
        value["expected_artifacts"] = tuple(value.get("expected_artifacts", ()))
        result = cls(**value)
        result.validate()
        return result


@dataclass(frozen=True)
class PluginManifest:
    plugin_id: str
    plugin_version: str
    adapter_entry: tuple[str, ...]
    capabilities: tuple[str, ...]
    supported_arch: tuple[str, ...]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    required_tools: tuple[str, ...] = ()
    default_timeout_seconds: int = 3600
    artifact_rules: tuple[dict[str, Any], ...] = ()
    environment: dict[str, str] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        _validate_version(self.schema_version)
        _validate_identifier("plugin_id", self.plugin_id)
        _validate_identifier("plugin_version", self.plugin_version)
        if not self.adapter_entry or not all(
            isinstance(item, str) and item for item in self.adapter_entry
        ):
            raise ValueError("adapter_entry must be a non-empty string list")
        if not self.capabilities or not all(
            isinstance(item, str) and item for item in self.capabilities
        ):
            raise ValueError("capabilities must be a non-empty string list")
        if not self.supported_arch or not all(
            isinstance(item, str) and item for item in self.supported_arch
        ):
            raise ValueError("supported_arch must be a non-empty string list")
        _validate_mapping("input_schema", self.input_schema)
        _validate_mapping("output_schema", self.output_schema)
        _validate_mapping("environment", self.environment)
        if not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in self.environment.items()
        ):
            raise ValueError("environment must contain only string values")
        if self.default_timeout_seconds <= 0:
            raise ValueError("default_timeout_seconds must be positive")

    def to_dict(self) -> dict[str, Any]:
        return _primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PluginManifest":
        value = _known_payload(cls, payload)
        for name in ("adapter_entry", "capabilities", "supported_arch", "required_tools"):
            value[name] = tuple(value.get(name, ()))
        value["artifact_rules"] = tuple(value.get("artifact_rules", ()))
        result = cls(**value)
        result.validate()
        return result


@dataclass(frozen=True)
class PluginResult:
    status: RuntimeStatus
    exit_code: int
    started_at: str
    ended_at: str
    metrics: tuple[dict[str, Any], ...] = ()
    artifacts: tuple[dict[str, Any], ...] = ()
    failure: dict[str, Any] | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        _validate_version(self.schema_version)
        if self.status not in {
            RuntimeStatus.SUCCEEDED,
            RuntimeStatus.FAILED,
            RuntimeStatus.CANCELLED,
            RuntimeStatus.TIMED_OUT,
        }:
            raise ValueError("PluginResult status must be terminal")
        if not isinstance(self.exit_code, int):
            raise ValueError("exit_code must be an integer")
        if self.status == RuntimeStatus.SUCCEEDED and self.exit_code != 0:
            raise ValueError("A succeeded PluginResult must have exit_code 0")
        if not self.started_at or not self.ended_at:
            raise ValueError("PluginResult timestamps are required")
        if self.failure is not None:
            _validate_mapping("failure", self.failure)
        _validate_mapping("provenance", self.provenance)
        for artifact in self.artifacts:
            _validate_mapping("artifact", artifact)
            if not isinstance(artifact.get("kind"), str) or not isinstance(
                artifact.get("path"), str
            ):
                raise ValueError("Each artifact requires string kind and path")

    def to_dict(self) -> dict[str, Any]:
        return _primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PluginResult":
        value = _known_payload(cls, payload)
        try:
            value["status"] = RuntimeStatus(value["status"])
        except (KeyError, ValueError) as exc:
            raise ValueError("Invalid PluginResult status") from exc
        value["metrics"] = tuple(value.get("metrics", ()))
        value["artifacts"] = tuple(value.get("artifacts", ()))
        result = cls(**value)
        result.validate()
        return result


@dataclass(frozen=True)
class ActionProposal:
    proposal_id: str
    producer: str
    target_run_id: str
    action_type: str
    parameters: dict[str, Any] = field(default_factory=dict)
    target_stage_key: str | None = None
    evidence_refs: tuple[str, ...] = ()
    risk: str = "low"
    budget: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        _validate_version(self.schema_version)
        for name, value in (
            ("proposal_id", self.proposal_id),
            ("producer", self.producer),
            ("target_run_id", self.target_run_id),
            ("action_type", self.action_type),
        ):
            _validate_identifier(name, value)
        _validate_identifier("target_stage_key", self.target_stage_key, required=False)
        _validate_mapping("parameters", self.parameters)
        _validate_mapping("budget", self.budget)
        if self.risk not in {"low", "medium", "high"}:
            raise ValueError("risk must be low, medium, or high")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ActionProposal":
        value = _known_payload(cls, payload)
        value["evidence_refs"] = tuple(value.get("evidence_refs", ()))
        result = cls(**value)
        result.validate()
        return result


@dataclass(frozen=True)
class Event:
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
        _validate_version(self.schema_version)
        for name, value in (
            ("event_id", self.event_id),
            ("run_id", self.run_id),
            ("event_type", self.event_type),
            ("producer", self.producer),
        ):
            _validate_identifier(name, value)
        _validate_identifier("stage_run_id", self.stage_run_id, required=False)
        _validate_identifier("attempt_id", self.attempt_id, required=False)
        if not self.occurred_at:
            raise ValueError("occurred_at is required")
        _validate_mapping("payload", self.payload)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Event":
        result = cls(**_known_payload(cls, payload))
        result.validate()
        return result
