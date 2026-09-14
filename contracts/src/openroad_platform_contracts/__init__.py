"""The platform's shared language.

Dependency-free by construction: this package imports nothing from the kernel,
an app, or a plugin, so every layer can depend on it without a cycle.  Guarded
by G1 (no vendor names) and G2 (no adapters).
"""

from .artifact import (
    Artifact,
    ArtifactDeclaration,
    Metric,
    RESERVED_ARTIFACT_KINDS,
    RESERVED_METRIC_CONTEXT_KEYS,
)
from .evaluation import (
    EvaluationRequest,
    EvaluatorPin,
    PROTECTED_EVALUATOR_CAPABILITY,
    ProtectedEvaluator,
    Verdict,
    VerdictStatus,
)
from .input import (
    INPUT_MANIFEST_FILENAME,
    INPUT_MANIFEST_KIND,
    InputFile,
    StagedInput,
)
from .progress import (
    DEFAULT_PROGRESS_MARKER,
    MAX_ENVELOPE_BYTES,
    ProgressPhase,
    ProgressReport,
    ProgressStatus,
    decode_progress_line,
)
from .resources import ResourceRequest
from .runtime import (
    ACTIVE_RUNTIME_STATUSES,
    ATTEMPT_TRANSITIONS,
    RUN_TRANSITIONS,
    AttemptStatus,
    Event,
    Failure,
    RuntimeStatus,
    TERMINAL_RUNTIME_STATUSES,
    attempt_transition_allowed,
    is_terminal,
    run_transition_allowed,
)
from .task import (
    PluginManifest,
    PluginResult,
    RuntimeRequirements,
    TaskSpec,
)
from .version import (
    ContractError,
    IDENTIFIER,
    SCHEMA_VERSION,
    SHA256_HEX,
    known_payload,
    primitive,
    validate_identifier,
    validate_mapping,
    validate_relative_path,
    validate_sha256,
    validate_version,
)

__all__ = (
    # versioning
    "ContractError", "IDENTIFIER", "SCHEMA_VERSION", "SHA256_HEX",
    "known_payload", "primitive", "validate_identifier", "validate_mapping",
    "validate_relative_path", "validate_sha256", "validate_version",
    # task triangle
    "PluginManifest", "PluginResult", "RuntimeRequirements", "TaskSpec",
    # resources
    "ResourceRequest",
    # staged inputs
    "INPUT_MANIFEST_FILENAME", "INPUT_MANIFEST_KIND", "InputFile", "StagedInput",
    # runtime state
    "ACTIVE_RUNTIME_STATUSES", "ATTEMPT_TRANSITIONS", "RUN_TRANSITIONS",
    "AttemptStatus", "Event", "Failure", "RuntimeStatus",
    "TERMINAL_RUNTIME_STATUSES", "attempt_transition_allowed", "is_terminal",
    "run_transition_allowed",
    # artifacts and evidence
    "Artifact", "ArtifactDeclaration", "Metric",
    "RESERVED_ARTIFACT_KINDS", "RESERVED_METRIC_CONTEXT_KEYS",
    # evaluation boundary
    "EvaluationRequest", "EvaluatorPin", "PROTECTED_EVALUATOR_CAPABILITY",
    "ProtectedEvaluator", "Verdict", "VerdictStatus",
    # progress envelope
    "DEFAULT_PROGRESS_MARKER", "MAX_ENVELOPE_BYTES", "ProgressPhase",
    "ProgressReport", "ProgressStatus", "decode_progress_line",
)
