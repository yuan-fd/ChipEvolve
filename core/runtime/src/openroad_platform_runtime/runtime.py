"""Workflow Runtime: the single authority over a run's lifecycle.

The kernel's job in one sentence: take an immutable ``TaskSpec``, run it in an
isolated workspace under a lease, validate what came back, ask the protected
evaluator what it measured, and record all of it.  It does not know which tool
ran, and it does not trust anything the adapter says about itself.

The two v1 defects this file exists to fix are fixed structurally, not patched:

* The old runtime branched on two literal plugin identifiers to decide whether a
  capability needed a frozen protocol receipt.  That is replaced by
  ``manifest.requirements.require_protocol_receipt``: a capability declares what
  it needs from the kernel as data, so a third capability gets the same
  treatment without the kernel being edited.

* The old runtime hardcoded one tool's six stage names in a regular expression.
  That is replaced by the generic ``ProgressObserver`` reading the progress
  contract.  The kernel learned the *shape* of a progress report and forgot the
  vocabulary; see ``openroad_platform_contracts.progress``.

The old code is preserved verbatim in the archived v1 tree for anyone comparing
behaviour.  It is deliberately not quoted here: a kernel that must name a vendor
in order to explain itself is still coupled to that vendor.
"""

from __future__ import annotations

import json
import platform
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from openroad_platform_contracts import (
    AttemptStatus,
    EvaluationRequest,
    Metric,
    PluginManifest,
    PluginResult,
    ProtectedEvaluator,
    RuntimeStatus,
    TaskSpec,
    Verdict,
    VerdictStatus,
    is_terminal,
)

from .adapter import AdapterExecution, ProcessAdapter, validate_artifact_declarations
from .digest import sha256
from .observer import ProgressObserver
from .store import (
    Attempt,
    InvalidTransition,
    RunRecord,
    RuntimeStore,
    RuntimeStoreError,
    StageRun,
)


class ManifestResolver(Protocol):
    """What the runtime needs from a plugin registry.

    Deliberately narrow.  The runtime resolves an id to a manifest; where that
    manifest came from -- a directory scan, a lock file, an approval record --
    is the registry's concern, not the kernel's.
    """

    def resolve(
        self, plugin_id: str, *, version: str | None = None,
        capability: str | None = None, arch: str | None = None,
    ) -> PluginManifest:
        ...  # pragma: no cover - protocol definition


@dataclass
class RuntimeConfig:
    workspace_root: Path
    lease_seconds: int = 30
    worker_id: str = ""

    def __post_init__(self) -> None:
        if self.lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.workspace_root = Path(self.workspace_root).expanduser().resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        if not self.worker_id:
            self.worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


#: Environment variable carrying the immutable protocol receipt to an adapter
#: is declared by the manifest, not concatenated by the kernel.
RECEIPT_ARTIFACT_KIND = "runtime_protocol_receipt"


class WorkflowRuntime:
    def __init__(
        self,
        store: RuntimeStore,
        resolver: ManifestResolver,
        *,
        config: RuntimeConfig | None = None,
        workspace_root: str | Path | None = None,
        adapter: ProcessAdapter | None = None,
        protected_evaluator: ProtectedEvaluator | None = None,
        environment_resolver: Callable[[RunRecord], dict[str, str]] | None = None,
        lease_seconds: int = 30,
        worker_id: str = "",
    ):
        if config is None:
            if workspace_root is None:
                raise ValueError("runtime needs a workspace_root or a config")
            config = RuntimeConfig(
                workspace_root=Path(workspace_root),
                lease_seconds=lease_seconds, worker_id=worker_id,
            )
        self.store = store
        self.resolver = resolver
        self.config = config
        self.adapter = adapter or ProcessAdapter()
        self.protected_evaluator = protected_evaluator
        self.environment_resolver = environment_resolver

    # -- submission -------------------------------------------------------

    def submit(
        self, task: TaskSpec, *, plugin_version: str | None = None,
        capability: str | None = None,
    ) -> RunRecord:
        task.validate()
        if task.plugin_id is None:
            raise ValueError("this runtime executes direct plugin tasks only")
        manifest = self.resolver.resolve(
            task.plugin_id, version=plugin_version, capability=capability,
            arch=platform.machine(),
        )
        return self.store.submit_run(
            task, stage_key="main", plugin_version=manifest.plugin_version,
        )

    def submit_idempotent(
        self, task: TaskSpec, *, plugin_version: str | None = None,
        capability: str | None = None,
    ) -> RunRecord:
        """Submit once by stable task id.

        A worker may restart after Runtime accepted a task but before it
        recorded the back-reference.  Reusing the id is safe only when the whole
        immutable TaskSpec matches; a different spec is an error, not an
        overwrite.
        """
        task.validate()
        if task.plugin_id is None:
            raise ValueError("this runtime executes direct plugin tasks only")
        manifest = self.resolver.resolve(
            task.plugin_id, version=plugin_version, capability=capability,
            arch=platform.machine(),
        )
        existing = self.store.find_run_by_task_id(task.task_id)
        if existing is None:
            return self.store.submit_run(
                task, stage_key="main",
                plugin_version=manifest.plugin_version,
            )
        if existing.task_spec.to_dict() != task.to_dict():
            raise RuntimeStoreError(
                f"task_id {task.task_id!r} already exists with a different "
                f"immutable TaskSpec"
            )
        stage = self.store.list_stages(existing.run_id)[0]
        if stage.plugin_version != manifest.plugin_version:
            raise RuntimeStoreError(
                f"task_id {task.task_id!r} already exists at plugin version "
                f"{stage.plugin_version!r}"
            )
        return existing

    # -- execution --------------------------------------------------------

    def execute_once(
        self, run_id: str, *,
        on_line: Callable[[str], None] | None = None,
        external_cancel_requested: Callable[[], bool] | None = None,
    ) -> RunRecord:
        """Advance a run by at most one attempt."""
        # Record an external cancellation in our own store first.  A caller may
        # observe that cancellation is wanted; it may not declare the outcome.
        if external_cancel_requested is not None and external_cancel_requested():
            self.store.request_cancel(run_id)

        run = self.store.get_run(run_id)
        if is_terminal(run.status):
            return run

        stage = self._next_ready_stage(run_id)
        if stage is None:
            return run

        manifest = self.resolver.resolve(
            stage.plugin_id, version=stage.plugin_version,
            arch=platform.machine(),
        )
        attempt_number = len(self.store.list_attempts(stage.stage_run_id)) + 1
        workspace = (
            self.config.workspace_root / run_id / stage.stage_run_id
            / f"attempt-{attempt_number}"
        )
        workspace.mkdir(parents=True, exist_ok=True)

        try:
            attempt = self.store.start_attempt(
                stage.stage_run_id, worker_id=self.config.worker_id,
                workspace=workspace, lease_seconds=self.config.lease_seconds,
            )
        except InvalidTransition:
            # Another worker won the race between selection and claim.  Return
            # the authoritative state instead of inventing a failure.
            return self.store.get_run(run_id)

        pulse = _LeasePulse(
            self.store, run_id, attempt.attempt_id,
            worker_id=self.config.worker_id,
            lease_seconds=self.config.lease_seconds,
            external_cancel_requested=external_cancel_requested,
        )
        observer = ProgressObserver(
            self.store, run_id=run_id, stage_run_id=stage.stage_run_id,
            attempt_id=attempt.attempt_id, marker=manifest.progress_marker,
            producer=f"adapter:{manifest.plugin_id}@{manifest.plugin_version}",
            downstream=on_line,
        )

        try:
            self._run_attempt(
                run, stage, attempt, manifest, workspace, pulse, observer,
            )
        except Exception as exc:
            self._record_runtime_failure(run, attempt, exc)
        finally:
            observer.record_summary()
        return self.store.get_run(run_id)

    def _run_attempt(
        self, run: RunRecord, stage: StageRun, attempt: Attempt,
        manifest: PluginManifest, workspace: Path,
        pulse: "_LeasePulse", observer: ProgressObserver,
    ) -> None:
        environment = dict(
            self.environment_resolver(run) if self.environment_resolver else {}
        )
        receipt = self._write_protocol_receipt(
            manifest, run, attempt, workspace, environment
        )

        execution = self.adapter.execute(
            manifest, run.task_spec, workspace=workspace,
            cancel_requested=pulse, on_line=observer, environment=environment,
        )
        self._reject_forged_authority(execution)

        if receipt is not None and receipt["sha256"] != sha256(receipt["path"]):
            raise RuntimeStoreError("adapter modified the runtime protocol receipt")

        # The receipt is the platform's own bookkeeping, so it is the one
        # caller allowed to register a reserved kind.
        runtime_artifacts = validate_artifact_declarations(
            workspace, manifest,
            (receipt["declaration"],) if receipt is not None else (),
            expected_kinds=(), require_expected=False, allow_reserved=True,
        )
        evaluator_artifacts = self._evaluate(
            execution, manifest, run, workspace, attempt.attempt_id
        )

        registered = (
            *runtime_artifacts, *execution.artifacts, *evaluator_artifacts
        )
        registered_ids = self.store.register_artifacts(
            attempt.attempt_id, workspace, registered
        )
        if execution.result.status is RuntimeStatus.SUCCEEDED:
            self._register_metrics(
                attempt, execution, registered, registered_ids
            )

        self.store.finish_attempt(
            attempt.attempt_id,
            _attempt_status(execution.result.status),
            exit_code=execution.result.exit_code,
            failure=execution.result.failure,
        )
        self.store.transition_run(
            run.run_id, execution.result.status,
            reason=_terminal_reason(execution.result),
        )

    def _write_protocol_receipt(
        self, manifest: PluginManifest, run: RunRecord, attempt: Attempt,
        workspace: Path, environment: dict[str, str],
    ) -> dict[str, Any] | None:
        """Freeze the experiment protocol into the attempt workspace.

        v1 hardcoded this for two plugin ids.  It is now driven by the manifest,
        so a third capability gets it without touching the kernel -- and the
        kernel never learns a vendor prefix.
        """
        requirements = manifest.requirements
        if not requirements.require_protocol_receipt:
            return None
        protocol = run.task_spec.inputs.get("experiment_protocol")
        if not isinstance(protocol, dict):
            raise RuntimeStoreError(
                f"{manifest.plugin_id!r} requires an immutable "
                f"experiment_protocol in inputs"
            )
        path = workspace / "runtime_protocol_receipt.json"
        path.write_text(json.dumps({
            "schema_version": 1,
            "protocol": protocol,
            "run_id": run.run_id,
            "attempt_id": attempt.attempt_id,
        }, sort_keys=True), encoding="utf-8")

        variable = requirements.environment_receipt_variable
        assert variable is not None  # guaranteed by RuntimeRequirements.validate
        digest = sha256(path)
        environment[variable] = str(path)
        environment[f"{variable}_SHA256"] = digest
        return {
            "path": path,
            "sha256": digest,
            "declaration": {
                "kind": RECEIPT_ARTIFACT_KIND,
                "path": path.name,
                "metadata": {"producer": "runtime",
                             "attempt_id": attempt.attempt_id},
            },
        }

    @staticmethod
    def _reject_forged_authority(execution: AdapterExecution) -> None:
        """An adapter may not manufacture the kernel's or evaluator's authority."""
        for item in execution.result.artifacts:
            if item.get("kind") == RECEIPT_ARTIFACT_KIND:
                raise RuntimeStoreError(
                    f"adapter declared runtime-reserved artifact kind "
                    f"{RECEIPT_ARTIFACT_KIND!r}"
                )
            metadata = item.get("metadata") or {}
            if metadata.get("official_qor") is not None:
                raise RuntimeStoreError(
                    "adapter declared a metric the platform reserves for the "
                    "protected evaluator"
                )
            if str(metadata.get("producer", "")).startswith("protected-"):
                raise RuntimeStoreError(
                    "adapter claimed a protected-evaluator producer identity"
                )

    def _evaluate(
        self, execution: AdapterExecution, manifest: PluginManifest,
        run: RunRecord, workspace: Path, attempt_id: str,
    ) -> tuple[dict[str, Any], ...]:
        if self.protected_evaluator is None:
            return ()
        if execution.result.status is not RuntimeStatus.SUCCEEDED:
            return ()
        verdict: Verdict = self.protected_evaluator.evaluate(EvaluationRequest(
            manifest=manifest, task=run.task_spec, workspace=str(workspace),
            attempt_id=attempt_id, declared_artifacts=(),
        ))
        verdict.validate()
        if verdict.status is VerdictStatus.REJECTED:
            raise RuntimeStoreError(
                f"protected evaluator rejected the run: {verdict.reason}"
            )
        return validate_artifact_declarations(
            workspace, manifest,
            [{"kind": a.kind, "path": a.path, "metadata": a.metadata}
             for a in verdict.artifacts],
            expected_kinds=(), require_expected=False,
        )

    def _register_metrics(
        self, attempt: Attempt, execution: AdapterExecution,
        registered: tuple[dict[str, Any], ...], registered_ids: list[str],
    ) -> None:
        """Attach each metric to the artifact it was read from.

        A metric whose source cannot be resolved is a protocol error: the
        platform will not store a number it cannot trace.
        """
        by_store_key = {
            item["store_key"]: artifact_id
            for item, artifact_id in zip(registered, registered_ids)
        }
        metrics = []
        for raw in execution.result.metrics:
            item = dict(raw)
            context = dict(item.get("context") or {})
            source_key = context.pop("source_artifact_store_key", None)
            if source_key is not None:
                artifact_id = by_store_key.get(source_key)
                if artifact_id is None:
                    raise RuntimeStoreError(
                        f"metric {item.get('name')!r} references an "
                        f"unregistered artifact {source_key!r}"
                    )
                item["source_artifact_id"] = artifact_id
            item["parser_id"] = context.pop("parser_id", None)
            item["parser_version"] = context.pop("parser_version", None)
            item["context"] = context
            item.pop("source_artifact_store_key", None)
            metrics.append(Metric(**item))
        self.store.register_metrics(attempt.attempt_id, metrics)

    def _record_runtime_failure(
        self, run: RunRecord, attempt: Attempt, exc: Exception
    ) -> None:
        """Terminate both the attempt and the run.

        Failing only the attempt leaves the run stuck in RUNNING forever, which
        is worse than a wrong answer: it is a run nothing will ever finish and
        nobody will ever see fail.
        """
        failure = {
            "category": "runtime_error",
            "message": f"{type(exc).__name__}: {exc}",
        }
        try:
            self.store.finish_attempt(
                attempt.attempt_id, AttemptStatus.FAILED,
                exit_code=1, failure=failure,
            )
        except InvalidTransition:
            # A lease monitor may already have moved RUNNING to LOST.  LOST is
            # authoritative evidence; do not overwrite it and do not crash.
            pass
        try:
            current = self.store.get_run(run.run_id)
            if current.status is RuntimeStatus.RUNNING:
                self.store.transition_run(
                    run.run_id, RuntimeStatus.FAILED,
                    reason=failure["category"],
                )
        except InvalidTransition:
            pass

    # -- reads ------------------------------------------------------------

    def describe(self, run_id: str) -> dict[str, Any]:
        return self.store.describe_run(run_id)

    def read_artifact_excerpt(
        self, run_id: str, artifact_id: str, *, offset: int, max_bytes: int,
    ) -> dict[str, Any]:
        """Read a registered artifact through the Runtime authority only.

        Callers never receive a workspace path or a store key.  The content is
        re-hashed before it is returned, so a file edited after registration is
        refused rather than served.
        """
        if (not isinstance(offset, int) or isinstance(offset, bool) or offset < 0
                or not isinstance(max_bytes, int) or isinstance(max_bytes, bool)
                or not 0 < max_bytes <= 64 * 1024):
            raise ValueError("artifact excerpt bounds are invalid")
        view = self.describe(run_id)
        matches = [
            (attempt, artifact)
            for stage in view.get("stages", ())
            for attempt in stage.get("attempts", ())
            for artifact in attempt.get("artifacts", ())
            if artifact.get("artifact_id") == artifact_id
        ]
        if len(matches) != 1:
            raise RuntimeStoreError(
                f"artifact {artifact_id!r} is not registered in run {run_id!r}"
            )
        attempt, artifact = matches[0]
        workspace = Path(str(attempt["workspace"])).resolve()
        path = (workspace / str(artifact["store_key"])).resolve()
        try:
            path.relative_to(workspace)
        except ValueError as exc:
            raise RuntimeStoreError(
                "registered artifact escapes the runtime workspace"
            ) from exc
        raw = path.read_bytes()
        if sha256(raw) != artifact["sha256"]:
            raise RuntimeStoreError(
                f"registered artifact {artifact_id!r} changed after registration"
            )
        return {
            "artifact_id": artifact_id,
            "sha256": str(artifact["sha256"]),
            "offset": offset,
            "text": raw[offset:offset + max_bytes].decode("utf-8", errors="replace"),
        }

    def _next_ready_stage(self, run_id: str) -> StageRun | None:
        for stage in self.store.list_stages(run_id):
            if stage.status in {RuntimeStatus.QUEUED, RuntimeStatus.RETRY_WAIT}:
                return stage
        return None


class _LeasePulse:
    """Callable handed to the guardian: returns True when we must stop.

    Doubles as the heartbeat.  A long run with no output still keeps its lease,
    which is what separates "slow" from "dead".
    """

    def __init__(
        self, store: RuntimeStore, run_id: str, attempt_id: str, *,
        worker_id: str, lease_seconds: int,
        external_cancel_requested: Callable[[], bool] | None = None,
    ):
        self.store = store
        self.run_id = run_id
        self.attempt_id = attempt_id
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.external_cancel_requested = external_cancel_requested
        self._last = 0.0

    def __call__(self) -> bool:
        if (self.external_cancel_requested is not None
                and self.external_cancel_requested()):
            self.store.request_cancel(self.run_id)
            return True
        if self.store.get_run(self.run_id).status is RuntimeStatus.CANCEL_REQUESTED:
            return True
        now = time.monotonic()
        if now - self._last >= max(1.0, self.lease_seconds / 3):
            self.store.heartbeat(
                self.attempt_id, worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )
            self._last = now
        return False


def _attempt_status(status: RuntimeStatus) -> AttemptStatus:
    return {
        RuntimeStatus.SUCCEEDED: AttemptStatus.SUCCEEDED,
        RuntimeStatus.CANCELLED: AttemptStatus.FAILED,
        RuntimeStatus.TIMED_OUT: AttemptStatus.FAILED,
    }.get(status, AttemptStatus.FAILED)


def _terminal_reason(result: PluginResult) -> str | None:
    if result.failure:
        return str(result.failure.get("category") or result.status.value)
    return None
