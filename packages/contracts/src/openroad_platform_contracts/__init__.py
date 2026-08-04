"""Stable contracts shared by the platform control and execution planes."""

from .models import (
    Artifact,
    ArtifactKind,
    ExecutionPlan,
    Metric,
    RunRequest,
    RunResult,
    RunStage,
    RunStatus,
    StageResult,
)
from .platform import (
    SCHEMA_VERSION,
    ActionProposal,
    Event,
    PluginManifest,
    PluginResult,
    RuntimeStatus,
    TaskSpec,
    TERMINAL_RUNTIME_STATUSES,
)

__all__ = [
    "Artifact",
    "ArtifactKind",
    "ExecutionPlan",
    "Metric",
    "RunRequest",
    "RunResult",
    "RunStage",
    "RunStatus",
    "StageResult",
    "SCHEMA_VERSION",
    "ActionProposal",
    "Event",
    "PluginManifest",
    "PluginResult",
    "RuntimeStatus",
    "TaskSpec",
    "TERMINAL_RUNTIME_STATUSES",
]
