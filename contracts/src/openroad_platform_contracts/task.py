"""The task/manifest/result triangle.

A capability enters the platform only through these three types.  The kernel
sees a manifest and a task; it never sees a vendor, a tool name, or an
algorithm.  Everything a plugin needs the kernel to do *differently* is
declared here as data, so the kernel never grows a branch per plugin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .runtime import RuntimeStatus
from .progress import DEFAULT_PROGRESS_MARKER
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


@dataclass(frozen=True)
class TaskSpec:
    """One reviewed, bounded request.

    Immutable once submitted.  ``inputs`` and ``parameters`` are free-form
    because the platform deliberately does not understand a capability's
    domain; the plugin's own ``input_schema`` constrains them.
    """

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
        validate_version(self.schema_version)
        for name in ("task_id", "project_id", "design_id"):
            validate_identifier(name, getattr(self, name))
        if (self.plugin_id is None) == (self.workflow_id is None):
            raise ContractError("TaskSpec must define exactly one of plugin_id or workflow_id")
        validate_identifier("plugin_id", self.plugin_id, required=False)
        validate_identifier("workflow_id", self.workflow_id, required=False)
        for name in ("inputs", "parameters", "resources", "labels"):
            validate_mapping(name, getattr(self, name))
        if not isinstance(self.timeout_seconds, int) or self.timeout_seconds <= 0:
            raise ContractError("timeout_seconds must be a positive integer")
        if not isinstance(self.max_attempts, int) or self.max_attempts <= 0:
            raise ContractError("max_attempts must be a positive integer")
        if not all(isinstance(i, str) and i for i in self.expected_artifacts):
            raise ContractError("expected_artifacts must contain non-empty strings")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TaskSpec":
        value = known_payload(cls, payload)
        value["expected_artifacts"] = tuple(value.get("expected_artifacts", ()))
        result = instantiate(cls, value)
        result.validate()
        return result


@dataclass(frozen=True)
class RuntimeRequirements:
    """What a plugin asks the kernel to provide.

    This is the replacement for ``if plugin_id in {...}`` branches inside the
    runtime.  A plugin that needs a frozen protocol receipt says so here; the
    kernel honours the flag without learning the plugin's name.

    ``environment_receipt_variable`` names the environment variable the kernel
    will populate with the receipt path, so the kernel never concatenates a
    vendor-specific prefix.
    """

    #: The kernel writes an immutable copy of ``inputs["experiment_protocol"]``
    #: into the attempt workspace and exposes its path to the adapter.
    require_protocol_receipt: bool = False
    #: Environment variable receiving the receipt path.  Required when the
    #: receipt is requested.
    environment_receipt_variable: str | None = None
    #: The task must carry ``inputs["experiment_protocol"]`` as a mapping.
    require_experiment_protocol: bool = False
    #: The kernel must reject the run if the adapter reports success without
    #: the protected evaluator having produced a verdict.
    require_protected_evaluation: bool = False

    def validate(self) -> None:
        if self.require_protocol_receipt and not self.environment_receipt_variable:
            raise ContractError(
                "require_protocol_receipt needs environment_receipt_variable"
            )
        if self.environment_receipt_variable is not None:
            validate_identifier(
                "environment_receipt_variable", self.environment_receipt_variable
            )
        if self.require_experiment_protocol and not self.require_protocol_receipt:
            raise ContractError(
                "require_experiment_protocol implies require_protocol_receipt"
            )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "RuntimeRequirements":
        if payload is None:
            return cls()
        import dataclasses

        data = dict(payload)
        data.pop("schema_version", None)
        allowed = {f.name for f in dataclasses.fields(cls)}
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ContractError(
                f"unknown RuntimeRequirements fields: {', '.join(unknown)}"
            )
        result = cls(**data)
        result.validate()
        return result


@dataclass(frozen=True)
class PluginManifest:
    """Immutable identity and boundary declaration for one capability."""

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
    requirements: RuntimeRequirements = field(default_factory=RuntimeRequirements)
    #: Line prefix the adapter uses to report stage progress.  The kernel parses
    #: the envelope generically; the stage vocabulary stays inside the plugin.
    progress_marker: str = DEFAULT_PROGRESS_MARKER
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        validate_version(self.schema_version)
        validate_identifier("plugin_id", self.plugin_id)
        validate_identifier("plugin_version", self.plugin_version)
        if not self.adapter_entry or not all(
            isinstance(i, str) and i for i in self.adapter_entry
        ):
            raise ContractError("adapter_entry must be a non-empty string list")
        if not self.capabilities or not all(
            isinstance(i, str) and i for i in self.capabilities
        ):
            raise ContractError("capabilities must be a non-empty string list")
        if not self.supported_arch or not all(
            isinstance(i, str) and i for i in self.supported_arch
        ):
            raise ContractError("supported_arch must be a non-empty string list")
        validate_mapping("input_schema", self.input_schema)
        validate_mapping("output_schema", self.output_schema)
        validate_mapping("environment", self.environment)
        if not all(isinstance(k, str) and isinstance(v, str)
                   for k, v in self.environment.items()):
            raise ContractError("environment must contain only string values")
        if self.default_timeout_seconds <= 0:
            raise ContractError("default_timeout_seconds must be positive")
        if not isinstance(self.progress_marker, str) or not self.progress_marker:
            raise ContractError("progress_marker must be a non-empty string")
        if len(self.progress_marker) > 32:
            raise ContractError("progress_marker is implausibly long")
        self.requirements.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PluginManifest":
        value = known_payload(cls, payload)
        for name in ("adapter_entry", "capabilities", "supported_arch", "required_tools"):
            value[name] = tuple(value.get(name, ()))
        value["artifact_rules"] = tuple(value.get("artifact_rules", ()))
        value["requirements"] = RuntimeRequirements.from_dict(value.get("requirements"))
        result = instantiate(cls, value)
        result.validate()
        return result


@dataclass(frozen=True)
class PluginResult:
    """What an adapter reports.  A proposal, not a verdict.

    The kernel re-checks exit code, schema, paths, hashes, and artifact kinds
    before any of this reaches durable state.
    """

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
        validate_version(self.schema_version)
        if self.status not in {
            RuntimeStatus.SUCCEEDED, RuntimeStatus.FAILED,
            RuntimeStatus.CANCELLED, RuntimeStatus.TIMED_OUT,
        }:
            raise ContractError("PluginResult status must be terminal")
        if not isinstance(self.exit_code, int):
            raise ContractError("exit_code must be an integer")
        if self.status is RuntimeStatus.SUCCEEDED and self.exit_code != 0:
            raise ContractError("a succeeded PluginResult must have exit_code 0")
        if not self.started_at or not self.ended_at:
            raise ContractError("PluginResult timestamps are required")
        if self.failure is not None:
            validate_mapping("failure", self.failure)
        validate_mapping("provenance", self.provenance)
        for artifact in self.artifacts:
            validate_mapping("artifact", artifact)
            if not isinstance(artifact.get("kind"), str) or not isinstance(
                artifact.get("path"), str
            ):
                raise ContractError("each artifact requires string kind and path")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PluginResult":
        value = known_payload(cls, payload)
        try:
            value["status"] = RuntimeStatus(value["status"])
        except (KeyError, ValueError) as exc:
            raise ContractError("invalid PluginResult status") from exc
        value["metrics"] = tuple(value.get("metrics", ()))
        value["artifacts"] = tuple(value.get("artifacts", ()))
        result = instantiate(cls, value)
        result.validate()
        return result
