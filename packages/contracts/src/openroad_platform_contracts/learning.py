"""Versioned contracts for evidence-backed learning and optimization.

These contracts deliberately keep observations, predictions, and shadow-policy
artifacts separate.  None of the objects in this module can execute a task.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .platform import IDENTIFIER, SCHEMA_VERSION


SHA256 = re.compile(r"^[0-9a-f]{64}$")
STAGE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _object(name: str, value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be a string-keyed object")


def _version(value: int) -> None:
    if value != SCHEMA_VERSION:
        raise ValueError(f"Unsupported schema_version {value!r}")


def _identifier(name: str, value: str) -> None:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid {name}: {value!r}")


def _number(name: str, value: Any, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or (nonnegative and result < 0):
        raise ValueError(f"{name} must be finite" + (" and nonnegative" if nonnegative else ""))
    return result


def _primitive(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {item.name: _primitive(getattr(value, item.name))
                for item in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_primitive(item) for item in value]
    return value


def _known(cls: type, payload: Mapping[str, Any]) -> dict[str, Any]:
    _object(cls.__name__, payload)
    allowed = {item.name for item in dataclasses.fields(cls)}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"Unknown {cls.__name__} fields: {', '.join(unknown)}")
    if "schema_version" not in payload:
        raise ValueError(f"{cls.__name__} requires schema_version")
    return dict(payload)


def _digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class EvidencePointer:
    ref: str
    sha256: str
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        _version(self.schema_version)
        if not isinstance(self.ref, str) or not self.ref.startswith(
            ("artifact:", "run:", "docs/evidence/", "source:")
        ):
            raise ValueError("EvidencePointer requires a durable reference")
        if not SHA256.fullmatch(self.sha256):
            raise ValueError("EvidencePointer requires a lowercase SHA-256")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvidencePointer":
        result = cls(**_known(cls, payload))
        result.validate()
        return result


@dataclass(frozen=True)
class LearningContext:
    design_id: str
    design_fingerprint: str
    platform: str
    pdk_id: str
    toolchain_id: str
    flow_stage: str
    metric_parser_version: str
    constraint_fingerprint: str = "0" * 64
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        _version(self.schema_version)
        for name, value in (("design_id", self.design_id), ("platform", self.platform),
                            ("pdk_id", self.pdk_id), ("toolchain_id", self.toolchain_id),
                            ("metric_parser_version", self.metric_parser_version)):
            _identifier(name, value)
        if not SHA256.fullmatch(self.design_fingerprint):
            raise ValueError("design_fingerprint must be a lowercase SHA-256")
        if not SHA256.fullmatch(self.constraint_fingerprint):
            raise ValueError("constraint_fingerprint must be a lowercase SHA-256")
        if not isinstance(self.flow_stage, str) or not STAGE.fullmatch(self.flow_stage):
            raise ValueError("Invalid flow_stage")

    @property
    def fingerprint(self) -> str:
        self.validate()
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LearningContext":
        result = cls(**_known(cls, payload))
        result.validate()
        return result


@dataclass(frozen=True)
class LearningObservation:
    observation_id: str
    context: LearningContext
    parameters: dict[str, float]
    metrics: dict[str, float]
    metric_units: dict[str, str]
    status: str
    cost_seconds: float
    run_id: str
    attempt_id: str
    evidence: tuple[EvidencePointer, ...]
    source: str = "observed"
    failure_category: str | None = None
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        _version(self.schema_version)
        _identifier("observation_id", self.observation_id)
        _identifier("run_id", self.run_id)
        _identifier("attempt_id", self.attempt_id)
        self.context.validate()
        _object("parameters", self.parameters)
        _object("metrics", self.metrics)
        _object("metric_units", self.metric_units)
        if self.source != "observed":
            raise ValueError("LearningObservation source must be observed")
        if self.status not in {"succeeded", "failed", "cancelled", "timed_out", "lost"}:
            raise ValueError("Invalid observation status")
        _number("cost_seconds", self.cost_seconds, nonnegative=True)
        for name, value in self.parameters.items():
            if not STAGE.fullmatch(name):
                raise ValueError(f"Invalid parameter name: {name!r}")
            _number(f"parameter {name}", value)
        for name, value in self.metrics.items():
            if not STAGE.fullmatch(name):
                raise ValueError(f"Invalid metric name: {name!r}")
            _number(f"metric {name}", value)
        if set(self.metric_units) - set(self.metrics):
            raise ValueError("metric_units contains an unknown metric")
        if self.status == "succeeded" and not self.metrics:
            raise ValueError("A succeeded observation requires metrics")
        if not self.evidence:
            raise ValueError("LearningObservation requires evidence")
        for item in self.evidence:
            item.validate()
        if self.failure_category is not None:
            _identifier("failure_category", self.failure_category)

    @property
    def fingerprint(self) -> str:
        self.validate()
        return _digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LearningObservation":
        value = _known(cls, payload)
        value["context"] = LearningContext.from_dict(value["context"])
        value["evidence"] = tuple(EvidencePointer.from_dict(item)
                                  for item in value.get("evidence", ()))
        result = cls(**value)
        result.validate()
        return result


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    lower: float
    upper: float
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        _version(self.schema_version)
        if not STAGE.fullmatch(self.name):
            raise ValueError("Invalid parameter name")
        low = _number("lower", self.lower)
        high = _number("upper", self.upper)
        if not low < high:
            raise ValueError("Parameter lower must be less than upper")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ParameterSpec":
        result = cls(**_known(cls, payload))
        result.validate()
        return result


@dataclass(frozen=True)
class ObjectiveSpec:
    metric_name: str
    direction: str
    weight: float = 1.0
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        _version(self.schema_version)
        if not STAGE.fullmatch(self.metric_name):
            raise ValueError("Invalid objective metric name")
        if self.direction not in {"min", "max"}:
            raise ValueError("Objective direction must be min or max")
        if _number("weight", self.weight, nonnegative=True) <= 0:
            raise ValueError("Objective weight must be positive")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ObjectiveSpec":
        result = cls(**_known(cls, payload))
        result.validate()
        return result


@dataclass(frozen=True)
class OptimizationStudy:
    study_id: str
    design_id: str
    context_fingerprint: str
    parameter_space: tuple[ParameterSpec, ...]
    objectives: tuple[ObjectiveSpec, ...]
    max_runs: int
    seed: int
    status: str = "planned"
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        _version(self.schema_version)
        _identifier("study_id", self.study_id)
        _identifier("design_id", self.design_id)
        if not SHA256.fullmatch(self.context_fingerprint):
            raise ValueError("context_fingerprint must be a lowercase SHA-256")
        if not self.parameter_space or len(self.parameter_space) > 16:
            raise ValueError("parameter_space must contain 1-16 parameters")
        if not self.objectives or len(self.objectives) > 16:
            raise ValueError("objectives must contain 1-16 metrics")
        for item in self.parameter_space:
            item.validate()
        for item in self.objectives:
            item.validate()
        if len({item.name for item in self.parameter_space}) != len(self.parameter_space):
            raise ValueError("parameter_space names must be unique")
        if len({item.metric_name for item in self.objectives}) != len(self.objectives):
            raise ValueError("objective names must be unique")
        if not isinstance(self.max_runs, int) or not 1 <= self.max_runs <= 64:
            raise ValueError("max_runs must be between 1 and 64")
        if not isinstance(self.seed, int) or self.seed < 0:
            raise ValueError("seed must be a nonnegative integer")
        if self.status not in {"planned", "active", "completed", "stopped"}:
            raise ValueError("Invalid study status")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OptimizationStudy":
        value = _known(cls, payload)
        value["parameter_space"] = tuple(ParameterSpec.from_dict(item)
                                         for item in value.get("parameter_space", ()))
        value["objectives"] = tuple(ObjectiveSpec.from_dict(item)
                                    for item in value.get("objectives", ()))
        result = cls(**value)
        result.validate()
        return result


@dataclass(frozen=True)
class Prediction:
    prediction_id: str
    study_id: str
    candidate_id: str
    metric_name: str
    mean: float
    stddev: float
    model_id: str
    context_fingerprint: str
    source: str = "predicted"
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        _version(self.schema_version)
        for name, value in (("prediction_id", self.prediction_id),
                            ("study_id", self.study_id),
                            ("candidate_id", self.candidate_id),
                            ("model_id", self.model_id)):
            _identifier(name, value)
        if not STAGE.fullmatch(self.metric_name):
            raise ValueError("Invalid prediction metric_name")
        _number("prediction mean", self.mean)
        _number("prediction stddev", self.stddev, nonnegative=True)
        if not SHA256.fullmatch(self.context_fingerprint):
            raise ValueError("Invalid prediction context fingerprint")
        if self.source != "predicted":
            raise ValueError("Prediction source must be predicted")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Prediction":
        result = cls(**_known(cls, payload))
        result.validate()
        return result


@dataclass(frozen=True)
class OptimizerProposal:
    proposal_id: str
    study_id: str
    candidate_id: str
    iteration: int
    parameters: dict[str, float]
    predictions: tuple[Prediction, ...]
    acquisition_value: float
    evidence: tuple[EvidencePointer, ...]
    execution_allowed: bool = False
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        _version(self.schema_version)
        for name, value in (("proposal_id", self.proposal_id),
                            ("study_id", self.study_id),
                            ("candidate_id", self.candidate_id)):
            _identifier(name, value)
        if not isinstance(self.iteration, int) or self.iteration < 0:
            raise ValueError("iteration must be a nonnegative integer")
        _object("parameters", self.parameters)
        for name, value in self.parameters.items():
            if not STAGE.fullmatch(name):
                raise ValueError("Invalid proposal parameter name")
            _number(f"parameter {name}", value)
        for prediction in self.predictions:
            prediction.validate()
            if prediction.study_id != self.study_id or prediction.candidate_id != self.candidate_id:
                raise ValueError("Prediction does not belong to proposal")
        _number("acquisition_value", self.acquisition_value)
        for item in self.evidence:
            item.validate()
        if self.execution_allowed is not False:
            raise ValueError("OptimizerProposal is data only and cannot execute")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OptimizerProposal":
        value = _known(cls, payload)
        value["predictions"] = tuple(Prediction.from_dict(item)
                                     for item in value.get("predictions", ()))
        value["evidence"] = tuple(EvidencePointer.from_dict(item)
                                  for item in value.get("evidence", ()))
        result = cls(**value)
        result.validate()
        return result


@dataclass(frozen=True)
class TrajectoryStep:
    trajectory_id: str
    step_index: int
    design_id: str
    context_fingerprint: str
    state: dict[str, float]
    action: dict[str, float]
    next_state: dict[str, float]
    reward_components: dict[str, float]
    reward: float
    terminal: bool
    run_id: str
    attempt_id: str
    evidence: tuple[EvidencePointer, ...]
    execution_allowed: bool = False
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        _version(self.schema_version)
        for name, value in (("trajectory_id", self.trajectory_id),
                            ("design_id", self.design_id), ("run_id", self.run_id),
                            ("attempt_id", self.attempt_id)):
            _identifier(name, value)
        if not isinstance(self.step_index, int) or self.step_index < 0:
            raise ValueError("step_index must be a nonnegative integer")
        if not SHA256.fullmatch(self.context_fingerprint):
            raise ValueError("Invalid trajectory context fingerprint")
        for label, values in (("state", self.state), ("action", self.action),
                              ("next_state", self.next_state),
                              ("reward_components", self.reward_components)):
            _object(label, values)
            for name, value in values.items():
                if not STAGE.fullmatch(name):
                    raise ValueError(f"Invalid {label} key")
                _number(f"{label} {name}", value)
        _number("reward", self.reward)
        if not isinstance(self.terminal, bool):
            raise ValueError("terminal must be boolean")
        if not self.evidence:
            raise ValueError("TrajectoryStep requires evidence")
        for item in self.evidence:
            item.validate()
        if self.execution_allowed is not False:
            raise ValueError("TrajectoryStep is offline data and cannot execute")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TrajectoryStep":
        value = _known(cls, payload)
        value["evidence"] = tuple(EvidencePointer.from_dict(item)
                                  for item in value.get("evidence", ()))
        result = cls(**value)
        result.validate()
        return result


@dataclass(frozen=True)
class ShadowPolicyProposal:
    proposal_id: str
    policy_id: str
    design_id: str
    context_fingerprint: str
    state: dict[str, float]
    action: dict[str, float]
    expected_return: float
    evidence: tuple[EvidencePointer, ...]
    execution_allowed: bool = False
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        _version(self.schema_version)
        for name, value in (("proposal_id", self.proposal_id),
                            ("policy_id", self.policy_id),
                            ("design_id", self.design_id)):
            _identifier(name, value)
        if not SHA256.fullmatch(self.context_fingerprint):
            raise ValueError("Invalid shadow-policy context fingerprint")
        for label, values in (("state", self.state), ("action", self.action)):
            _object(label, values)
            for key, value in values.items():
                if not STAGE.fullmatch(key):
                    raise ValueError(f"Invalid {label} key")
                _number(f"{label} {key}", value)
        _number("expected_return", self.expected_return)
        if not self.evidence:
            raise ValueError("ShadowPolicyProposal requires evidence")
        for item in self.evidence:
            item.validate()
        if self.execution_allowed is not False:
            raise ValueError("ShadowPolicyProposal cannot execute")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ShadowPolicyProposal":
        value = _known(cls, payload)
        value["evidence"] = tuple(EvidencePointer.from_dict(item)
                                  for item in value.get("evidence", ()))
        result = cls(**value)
        result.validate()
        return result


@dataclass(frozen=True)
class MechanismEvidence:
    evidence_id: str
    level: str
    scope: str
    mechanism_intent: str
    source_reference: dict[str, Any]
    controls: dict[str, Any]
    liveness: dict[str, Any]
    baseline_metrics: dict[str, float]
    candidate_metrics: dict[str, float]
    legality: bool
    full_flow_validated: bool
    review_outcome: str
    compatibility_observations: tuple[str, ...]
    evidence: tuple[EvidencePointer, ...]
    execution_allowed: bool = False
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        _version(self.schema_version)
        _identifier("evidence_id", self.evidence_id)
        if self.level not in {"calibration", "target_local"}:
            raise ValueError("Invalid mechanism evidence level")
        if self.scope not in {"legalization", "dpo", "handoff", "mirroring",
                              "workflow", "other"}:
            raise ValueError("Invalid mechanism scope")
        if not isinstance(self.mechanism_intent, str) or not self.mechanism_intent.strip():
            raise ValueError("mechanism_intent is required")
        _object("source_reference", self.source_reference)
        if self.source_reference.get("kind") not in {
            "patch", "branch", "commit", "source_start", "donor", "artifact",
        } or not isinstance(self.source_reference.get("ref"), str):
            raise ValueError("Invalid mechanism source_reference")
        files = self.source_reference.get("files", [])
        if not isinstance(files, list) or not all(isinstance(item, str) and item for item in files):
            raise ValueError("source_reference files must be strings")
        _object("controls", self.controls)
        _object("liveness", self.liveness)
        if self.liveness.get("status") not in {"live", "inactive", "unknown"}:
            raise ValueError("Invalid mechanism liveness status")
        counters = self.liveness.get("counters", {})
        if not isinstance(counters, Mapping):
            raise ValueError("Mechanism liveness counters must be an object")
        _object("baseline_metrics", self.baseline_metrics)
        _object("candidate_metrics", self.candidate_metrics)
        for label, metrics in (("baseline", self.baseline_metrics),
                               ("candidate", self.candidate_metrics)):
            for name, value in metrics.items():
                if not STAGE.fullmatch(name):
                    raise ValueError(f"Invalid {label} metric name")
                _number(f"{label} metric {name}", value)
        if not isinstance(self.legality, bool) or not isinstance(self.full_flow_validated, bool):
            raise ValueError("legality and full_flow_validated must be boolean")
        if self.review_outcome not in {
            "parent_promotion", "mechanism_evidence", "repair_lead",
            "negative_evidence", "candidate_rejection",
        }:
            raise ValueError("Invalid mechanism review_outcome")
        if not all(isinstance(item, str) and item for item in self.compatibility_observations):
            raise ValueError("Invalid compatibility observations")
        if not self.evidence:
            raise ValueError("MechanismEvidence requires evidence")
        for item in self.evidence:
            item.validate()
        if self.execution_allowed is not False:
            raise ValueError("MechanismEvidence cannot execute")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _primitive(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MechanismEvidence":
        value = _known(cls, payload)
        value["compatibility_observations"] = tuple(
            value.get("compatibility_observations", ())
        )
        value["evidence"] = tuple(EvidencePointer.from_dict(item)
                                  for item in value.get("evidence", ()))
        result = cls(**value)
        result.validate()
        return result
