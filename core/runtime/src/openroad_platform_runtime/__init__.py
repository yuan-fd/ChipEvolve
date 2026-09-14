"""The platform kernel's runtime.

Owns: attempts, leases, workspaces, timeouts, cancellation, recovery, artifact
registration, and the protected-evaluation boundary.

Does not own: any knowledge of a specific tool, vendor, or algorithm.  Guarded
by G1 and G2 -- see AGENTS.md.
"""

from .adapter import (
    AdapterExecution,
    AdapterProtocolError,
    LOG_FILENAME,
    ProcessAdapter,
    REQUEST_FILENAME,
    RESULT_FILENAME,
    protocol_failure,
    validate_artifact_declarations,
)
from .digest import sha256
from .guardian import ProcessGuardian, ProcessOutcome
from .observer import ProgressObserver
from .runtime import (
    InputStagingError,
    ResourceLimitsUnsupported,
    ManifestResolver,
    RECEIPT_ARTIFACT_KIND,
    RuntimeConfig,
    WorkflowRuntime,
)
from .worker import CycleReport, RuntimeWorker
from .store import (
    Attempt,
    InvalidTransition,
    RunRecord,
    RuntimeStore,
    RuntimeStoreError,
    StageRun,
)

__all__ = (
    # durable state
    "Attempt", "InvalidTransition", "RunRecord", "RuntimeStore",
    "RuntimeStoreError", "StageRun",
    # process supervision
    "ProcessGuardian", "ProcessOutcome",
    # adapter protocol
    "AdapterExecution", "AdapterProtocolError", "ProcessAdapter",
    "REQUEST_FILENAME", "RESULT_FILENAME", "LOG_FILENAME",
    "protocol_failure", "validate_artifact_declarations",
    # orchestration
    "InputStagingError", "ManifestResolver", "ResourceLimitsUnsupported",
    "RuntimeConfig", "WorkflowRuntime",
    "RECEIPT_ARTIFACT_KIND",
    "ProgressObserver",
    # stage progress
    "CycleReport", "RuntimeWorker",
    # the one digest
    "sha256",
)
