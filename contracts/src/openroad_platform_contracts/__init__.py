"""The platform's shared language.

Dependency-free by construction: this package imports nothing from the kernel,
an app, or a plugin, so every layer can depend on it without a cycle.  Guarded
by G1 (no vendor names) and G2 (no adapters).
"""

from .artifact import (
    RESERVED_ARTIFACT_KINDS,
    RESERVED_METRIC_CONTEXT_KEYS,
    Artifact,
    ArtifactDeclaration,
    Metric,
)
from .evaluation import (
    PROTECTED_EVALUATOR_CAPABILITY,
    EvaluationRequest,
    EvaluatorPin,
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
    TERMINAL_RUNTIME_STATUSES,
    AttemptStatus,
    Event,
    Failure,
    RuntimeStatus,
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
    IDENTIFIER,
    SCHEMA_VERSION,
    SHA256_HEX,
    ContractError,
    known_payload,
    primitive,
    validate_identifier,
    validate_mapping,
    validate_relative_path,
    validate_sha256,
    validate_version,
)

__all__ = (
    "ACTIVE_RUNTIME_STATUSES", "ATTEMPT_TRANSITIONS", "DEFAULT_PROGRESS_MARKER",
    "IDENTIFIER", "INPUT_MANIFEST_FILENAME", "INPUT_MANIFEST_KIND",
    "MAX_ENVELOPE_BYTES", "PROTECTED_EVALUATOR_CAPABILITY",
    "RESERVED_ARTIFACT_KINDS", "RESERVED_METRIC_CONTEXT_KEYS", "RUN_TRANSITIONS",
    "SCHEMA_VERSION", "SHA256_HEX", "TERMINAL_RUNTIME_STATUSES",
    "Artifact", "ArtifactDeclaration", "AttemptStatus", "ContractError",
    "EvaluationRequest", "EvaluatorPin", "Event", "Failure", "InputFile",
    "Metric", "PluginManifest", "PluginResult", "ProgressPhase", "ProgressReport",
    "ProgressStatus", "ProtectedEvaluator", "ResourceRequest", "RuntimeRequirements",
    "RuntimeStatus", "StagedInput", "TaskSpec", "Verdict", "VerdictStatus",
    "attempt_transition_allowed", "decode_progress_line", "is_terminal", "known_payload",
    "primitive", "run_transition_allowed", "validate_identifier", "validate_mapping",
    "validate_relative_path", "validate_sha256", "validate_version",
)
