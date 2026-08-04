"""Versioned records used by the flow-level gray-box evidence layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


SCHEMA_VERSION = 2


@dataclass
class EvidenceRef:
    path: str
    kind: str
    line: int | None = None
    excerpt: str | None = None
    sha256: str | None = None
    confidence: str = "unconfirmed"


@dataclass
class ParameterRecord:
    display_name: str
    web_field: str
    internal_name: str
    orfs_name: str
    default: Any
    value: Any
    data_type: str
    allowed: dict | list | None
    unit: str
    platforms: list[str]
    stage: str
    substage: str | None
    engineering_definition: str
    plain_explanation: str
    affected_metrics: list[str]
    risks: list[str]
    confidence: str
    chain: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)


@dataclass
class ArtifactRecord:
    artifact_id: str
    experiment_id: str
    design: str
    platform: str
    stage: str | None
    substage: str | None
    artifact_type: str
    path: str
    size: int | None
    generated_at: str | None
    sha256: str | None
    source: str | None
    exists: bool
    previewable: bool
    current_run: bool


@dataclass
class SubstageRecord:
    substage_id: str
    display_name: str
    stage: str
    status: str
    script: str
    make_target: str
    command: str | None
    started_at: str | None
    finished_at: str | None
    runtime_seconds: float | None
    inputs: list[str]
    outputs: list[str]
    logs: list[str]
    reports: list[str]
    metrics: dict = field(default_factory=dict)
    metric_sources: dict = field(default_factory=dict)
    warning_count: int = 0
    error_count: int = 0
    gate_status: str = "unknown"
    declared_commands: list[str] = field(default_factory=list)
    command_evidence: list[dict] = field(default_factory=list)
    skip_reason: str | None = None


@dataclass
class LogEvent:
    event_id: str
    timestamp: str | None
    stage: str
    substage: str | None
    severity: str
    tool: str | None
    category: str
    message: str
    source_file: str
    source_line: int
    related_parameter: str | None
    blocking: bool
    suggested_check: str | None


@dataclass
class EvaluationGate:
    gate_id: str
    title: str
    status: str
    blocking: bool
    message: str
    evidence: list[dict] = field(default_factory=list)


def record_dict(record) -> dict:
    return asdict(record)
