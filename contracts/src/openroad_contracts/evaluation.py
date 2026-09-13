"""The protected evaluation boundary.

The evaluator decides QoR.  Nothing else may.  A model's opinion, a dashboard
number, or an adapter's self-reported metric are all non-authoritative.

Design decision worth stating explicitly: the kernel owns the *boundary*, not
the *domain*.  It knows how to invoke an evaluator, how to validate what comes
back, and how to refuse a forged verdict.  It does not know what a metric is
called or how to parse a tool's output -- that is a plugin, pinned by hash.

v1 put the parsers inside the platform and the result was a control plane
containing twenty vendor-specific identifiers.  The boundary is the platform's
job; the vocabulary is the domain's job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, runtime_checkable

from .artifact import ArtifactDeclaration, Metric
from .task import PluginManifest, TaskSpec
from .version import (
    ContractError,
    SCHEMA_VERSION,
    primitive,
    validate_identifier,
    validate_mapping,
    validate_sha256,
    validate_version,
)

#: Capability a plugin must declare to be eligible as the protected evaluator.
PROTECTED_EVALUATOR_CAPABILITY = "evaluate.protected"


class VerdictStatus(str, Enum):
    ADMISSIBLE = "admissible"
    REJECTED = "rejected"
    INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class Verdict:
    """The evaluator's answer.

    ``INCOMPLETE`` is the honest answer when a required input was missing.  It
    exists so that "we could not measure this" never has to be reported as a
    number.
    """

    status: VerdictStatus
    metrics: tuple[Metric, ...] = ()
    artifacts: tuple[ArtifactDeclaration, ...] = ()
    reason: str | None = None
    loss_manifest: dict[str, Any] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        validate_version(self.schema_version)
        if self.status is VerdictStatus.ADMISSIBLE and not self.metrics:
            raise ContractError(
                "an admissible verdict must carry at least one metric"
            )
        if self.status is not VerdictStatus.ADMISSIBLE and not self.reason:
            raise ContractError(
                "a non-admissible verdict must state a reason"
            )
        for metric in self.metrics:
            metric.validate()
            if metric.source_artifact_id is None:
                raise ContractError(
                    f"metric {metric.name!r} has no source artifact; "
                    f"an unsourced metric is not evidence"
                )
        for artifact in self.artifacts:
            artifact.validate()
        validate_mapping("loss_manifest", self.loss_manifest)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return primitive(self)


@dataclass(frozen=True)
class EvaluationRequest:
    """Everything an evaluator is allowed to look at.

    Deliberately narrow: the manifest (to know what was promised), the task (to
    know what was asked), and the workspace path.  No store handle, no network,
    no other run's state.
    """

    manifest: PluginManifest
    task: TaskSpec
    workspace: str
    attempt_id: str
    declared_artifacts: tuple[ArtifactDeclaration, ...] = ()

    def validate(self) -> None:
        self.manifest.validate()
        self.task.validate()
        if not isinstance(self.workspace, str) or not self.workspace:
            raise ContractError("workspace is required")
        validate_identifier("attempt_id", self.attempt_id)


@runtime_checkable
class ProtectedEvaluator(Protocol):
    """What the kernel requires of an evaluator.

    Implementations live in ``plugins/`` and are pinned by manifest hash.  The
    kernel invokes this and validates the reply; it never reaches into the
    implementation.
    """

    def evaluate(self, request: EvaluationRequest) -> Verdict:
        ...  # pragma: no cover - protocol definition


@dataclass(frozen=True)
class EvaluatorPin:
    """The kernel's record of which evaluator is authoritative.

    A run's QoR is only comparable to another run's QoR if the same evaluator
    version produced both.  Recording the pin alongside the result is what
    makes that checkable later.
    """

    plugin_id: str
    plugin_version: str
    manifest_sha256: str

    def validate(self) -> None:
        validate_identifier("plugin_id", self.plugin_id)
        validate_identifier("plugin_version", self.plugin_version)
        validate_sha256("manifest_sha256", self.manifest_sha256)

    @classmethod
    def of(cls, manifest: PluginManifest) -> "EvaluatorPin":
        import hashlib
        import json

        if PROTECTED_EVALUATOR_CAPABILITY not in manifest.capabilities:
            raise ContractError(
                f"plugin {manifest.plugin_id!r} does not declare "
                f"{PROTECTED_EVALUATOR_CAPABILITY!r}"
            )
        payload = json.dumps(manifest.to_dict(), sort_keys=True).encode("utf-8")
        pin = cls(
            plugin_id=manifest.plugin_id,
            plugin_version=manifest.plugin_version,
            manifest_sha256=hashlib.sha256(payload).hexdigest(),
        )
        pin.validate()
        return pin
