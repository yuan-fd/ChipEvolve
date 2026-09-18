"""The platform kernel's runtime.

Owns: attempts, leases, workspaces, timeouts, cancellation, recovery, artifact
registration, and the protected-evaluation boundary.

Does not own: any knowledge of a specific tool, vendor, or algorithm.  Guarded
by G1 and G2 -- see AGENTS.md.
"""

from .adapter import (
    LOG_FILENAME,
    REQUEST_FILENAME,
    RESULT_FILENAME,
    AdapterExecution,
    AdapterProtocolError,
    ProcessAdapter,
    protocol_failure,
    validate_artifact_declarations,
)
from .artifact_inventory import ArtifactInventory
from .digest import sha256
from .guardian import ProcessGuardian, ProcessOutcome
from .log_query import LogQuery
from .observer import ProgressObserver
from .resource_query import ResourceQuery
from .runtime import (
    RECEIPT_ARTIFACT_KIND,
    InputStagingError,
    ManifestResolver,
    ResourceLimitsUnsupported,
    RuntimeConfig,
    WorkflowRuntime,
)
from .store import (
    Attempt,
    InvalidTransition,
    RunRecord,
    RuntimeStore,
    RuntimeStoreError,
    StageRun,
    UploadedInput,
)
from .worker import CycleReport, RuntimeWorker

__all__ = (
    "LOG_FILENAME", "RECEIPT_ARTIFACT_KIND", "REQUEST_FILENAME", "RESULT_FILENAME",
    "AdapterExecution", "AdapterProtocolError", "ArtifactInventory", "Attempt",
    "CycleReport", "InputStagingError", "InvalidTransition", "LogQuery",
    "ManifestResolver", "ProcessAdapter", "ProcessGuardian", "ProcessOutcome",
    "ProgressObserver", "ResourceLimitsUnsupported", "ResourceQuery", "RunRecord",
    "RuntimeConfig", "RuntimeStore", "RuntimeStoreError", "RuntimeWorker", "StageRun",
    "UploadedInput",
    "WorkflowRuntime", "protocol_failure", "sha256", "validate_artifact_declarations",
)
