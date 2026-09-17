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

The old code is preserved in the archived v1 tree for comparison; it is not
quoted here because a kernel that names a vendor is still coupled to it.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from openroad_platform_contracts import (
    INPUT_MANIFEST_FILENAME,
    INPUT_MANIFEST_KIND,
    SCHEMA_VERSION,
    AttemptStatus,
    EvaluationRequest,
    InputFile,
    Metric,
    PluginManifest,
    PluginResult,
    ProtectedEvaluator,
    ResourceRequest,
    RuntimeStatus,
    StagedInput,
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


class InputStagingError(RuntimeError):
    """The platform could not put a declared input where the task said.

    A caller error, not a plugin's: the task named bytes that are not there, or
    named them at a path the platform may not use.  Reported before a run exists
    so the caller is told their request is wrong rather than handed a run that
    is about to fail.
    """


class ResourceLimitsUnsupported(RuntimeError):
    """The task declared bounds this host cannot enforce.

    A refusal rather than a warning.  A limit that is accepted and not applied
    is the exact species of false promise the rest of this codebase keeps
    deleting: the caller believes the machine is protected, and it is not.
    """


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
    capacity_cpu_cores: int | None = None
    capacity_memory_bytes: int | None = None
    platform_fraction: float = 0.60
    #: What a task that declares nothing still reserves.  Without this, a
    #: task could opt out of the scheduler entirely by staying silent.
    default_task_cpu_cores: int = 1
    default_task_memory_bytes: int = 1 << 30

    def reservation_for(self, task: TaskSpec) -> ResourceRequest:
        """What this task will reserve, whether or not it asked for anything.

        One implementation, because two would disagree: the runtime reserves on
        this basis, and the query layer explains a waiting run on the same
        basis.  A run whose explanation used different numbers than its
        reservation would send an operator looking for a shortfall that is not
        there.
        """
        requested = task.resources
        return ResourceRequest(
            cpu_cores=(requested.cpu_cores
                       if requested and requested.cpu_cores is not None
                       else self.default_task_cpu_cores),
            memory_bytes=(requested.memory_bytes
                          if requested and requested.memory_bytes is not None
                          else self.default_task_memory_bytes),
        )

    def __post_init__(self) -> None:
        if self.lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if self.capacity_cpu_cores is not None and self.capacity_cpu_cores < 1:
            raise ValueError("capacity_cpu_cores must be positive")
        if self.capacity_memory_bytes is not None and self.capacity_memory_bytes < 1:
            raise ValueError("capacity_memory_bytes must be positive")
        if self.capacity_cpu_cores is None:
            self.capacity_cpu_cores = os.cpu_count() or 1
        if self.capacity_memory_bytes is None:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            self.capacity_memory_bytes = int(pages * page_size)
        if self.default_task_cpu_cores < 1 or self.default_task_memory_bytes < 1:
            raise ValueError("default task resource reservations must be positive")
        if not 0 < self.platform_fraction <= 1:
            raise ValueError("platform_fraction must be in (0, 1]")
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
        self._check_inputs(task)
        self._check_resources(task)
        if task.plugin_id is None:
            raise ValueError("this runtime executes direct plugin tasks only")
        manifest = self.resolver.resolve(
            task.plugin_id, version=plugin_version, capability=capability,
            arch=platform.machine(),
        )
        return self.store.submit_run(
            task, stage_key="main", plugin_version=manifest.plugin_version,
            resumable=manifest.requirements.resumable,
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
        self._check_inputs(task)
        self._check_resources(task)
        if task.plugin_id is None:
            raise ValueError("this runtime executes direct plugin tasks only")
        manifest = self.resolver.resolve(
            task.plugin_id, version=plugin_version, capability=capability,
            arch=platform.machine(),
        )
        return self.store.submit_run(
            task, stage_key="main", plugin_version=manifest.plugin_version,
            idempotent=True, resumable=manifest.requirements.resumable,
        )

    # -- inputs -----------------------------------------------------------

    def _check_resources(self, task: TaskSpec) -> None:
        """Refuse a task whose declared limits this host cannot enforce."""
        resources = task.resources
        if resources is None or not resources.declared:
            return
        if self.config.capacity_cpu_cores is not None and resources.cpu_cores is not None \
                and resources.cpu_cores > int(self.config.capacity_cpu_cores * self.config.platform_fraction):
            raise ValueError("requested cpu_cores exceed platform budget")
        if self.config.capacity_memory_bytes is not None and resources.memory_bytes is not None \
                and resources.memory_bytes > int(self.config.capacity_memory_bytes * self.config.platform_fraction):
            raise ValueError("requested memory exceeds platform budget")
        if not self.adapter.supports_limits():
            raise ResourceLimitsUnsupported(
                f"this host cannot measure a process tree, so the requested "
                f"limits ({resources.describe()}) cannot be enforced"
            )

    def _check_inputs(self, task: TaskSpec) -> None:
        """Refuse a task whose declared inputs cannot be honoured.

        For a host path this is a ``stat``, not a digest: the point is to tell
        the caller their request is wrong while they are still listening.  The
        bytes are measured later, as they are copied, because that copy is what
        the adapter will read.  The file may still vanish in between -- and then
        the attempt fails with a recorded reason, which is the honest outcome
        rather than a guarantee this platform cannot make.

        For an artifact reference only the *row* is checked here, for the same
        reason.  An id the platform has never registered is a malformed request
        whether the input is required or not, so it is refused either way; an
        id it has registered but whose bytes have gone is an integrity problem
        and is discovered as the bytes are copied.
        """
        for declaration in task.staged_inputs:
            if declaration.artifact_id is not None:
                try:
                    self.store.get_artifact(declaration.artifact_id)
                except RuntimeStoreError as exc:
                    raise InputStagingError(
                        f"input references an artifact the platform does not "
                        f"have: {declaration.artifact_id!r}"
                    ) from exc
                continue
            if declaration.required and not Path(declaration.source).is_file():
                raise InputStagingError(
                    f"required input is not a readable file: "
                    f"{declaration.source!r} (declared as "
                    f"{declaration.destination!r})"
                )

    def _stage_inputs(
        self, task: TaskSpec, workspace: Path
    ) -> tuple[tuple[StagedInput, ...], dict[str, Any] | None]:
        """Place the declared inputs in the workspace and measure what landed.

        Copy, not link.  A hardlink would let an adapter corrupt the caller's
        original *through its own input*, and a symlink would let it read
        outside the workspace -- and there is no sandbox to stop either.  The
        honest cost is one copy per attempt rather than one per run; it is
        recorded here instead of being discovered when the first large design
        arrives.

        The digest is taken from the destination, not the source, for the same
        reason ``register_artifacts`` hashes what is on disk: a hash of what was
        *supposed* to be copied would verify the intention, not the bytes.
        """
        if not task.staged_inputs:
            return (), None

        staged: list[StagedInput] = []
        for declaration in task.staged_inputs:
            destination = workspace / declaration.destination
            if declaration.artifact_id is not None:
                staged.append(
                    self._stage_from_artifact(declaration, destination)
                )
                continue
            source = Path(declaration.source)
            if not source.is_file():
                if declaration.required:
                    raise InputStagingError(
                        f"required input disappeared before it could be staged: "
                        f"{declaration.source!r}"
                    )
                staged.append(StagedInput(
                    destination=declaration.destination,
                    source=declaration.source, present=False, size_bytes=0,
                ))
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            staged.append(StagedInput(
                destination=declaration.destination,
                source=declaration.source, present=True,
                size_bytes=destination.stat().st_size,
                sha256=sha256(destination),
            ))

        manifest_path = workspace / INPUT_MANIFEST_FILENAME
        manifest_path.write_text(json.dumps({
            "schema_version": SCHEMA_VERSION,
            # An identity document, not an audit record.  Where a file happened
            # to live is not part of what it is: two runs that read the same
            # bytes into the same destinations are running the same design even
            # if one caller kept it under /tmp and the other on a shared volume.
            # Including the source would make those two manifests differ, and
            # the digest would then measure the caller's filing habits.  The
            # source is recorded in ``runtime_inputs``, where it is provenance.
            "inputs": [
                {"destination": item.destination, "present": item.present,
                 "size_bytes": item.size_bytes, "sha256": item.sha256}
                for item in staged
            ],
        }, indent=2, sort_keys=True), encoding="utf-8")
        return tuple(staged), {
            "path": manifest_path,
            "sha256": sha256(manifest_path),
            "declaration": {
                "kind": INPUT_MANIFEST_KIND,
                "path": manifest_path.name,
                "metadata": {"producer": "runtime"},
            },
        }

    def _stage_from_artifact(
        self, declaration: InputFile, destination: Path
    ) -> StagedInput:
        """Place bytes the platform already holds, and check them as they land.

        The copy is verified against the artifact's own record rather than
        trusted because it came from the object store.  That check is the whole
        reason a reference is worth having: a run that says "this came from
        artifact X" becomes a claim someone can falsify, and this is where it is
        checked.
        """
        try:
            artifact = self.store.materialize_artifact(
                declaration.artifact_id, destination
            )
        except RuntimeStoreError as exc:
            if declaration.required:
                raise InputStagingError(str(exc)) from exc
            return StagedInput(
                destination=declaration.destination, present=False, size_bytes=0,
                source_artifact_id=declaration.artifact_id,
            )
        digest = sha256(destination)
        size_bytes = destination.stat().st_size
        if digest != artifact.sha256 or size_bytes != artifact.size_bytes:
            raise InputStagingError(
                f"artifact {declaration.artifact_id!r} does not match its own "
                f"record: recorded {artifact.sha256} ({artifact.size_bytes} "
                f"bytes), copied {digest} ({size_bytes} bytes)"
            )
        return StagedInput(
            destination=declaration.destination, present=True,
            size_bytes=size_bytes, sha256=digest,
            source_artifact_id=declaration.artifact_id,
        )

    # -- execution --------------------------------------------------------

    def execute_once(
        self, run_id: str, *,
        on_line: Callable[[str], None] | None = None,
        external_cancel_requested: Callable[[], bool] | None = None,
    ) -> RunRecord:
        """Advance a run by at most one attempt."""
        run, _ = self.execute_once_reporting(
            run_id, on_line=on_line,
            external_cancel_requested=external_cancel_requested,
        )
        return run

    def execute_once_reporting(
        self, run_id: str, *,
        on_line: Callable[[str], None] | None = None,
        external_cancel_requested: Callable[[], bool] | None = None,
    ) -> tuple[RunRecord, bool]:
        """Advance a run, and say whether *this* call did the work.

        The boolean exists because a caller cannot work it out for itself.
        A worker that lost the race for the lease still observes the run move,
        so any check it makes -- status changed, attempt count grew -- reports
        work it did not do.  Only this method knows.
        """
        # Record an external cancellation in our own store first.  A caller may
        # observe that cancellation is wanted; it may not declare the outcome.
        if external_cancel_requested is not None and external_cancel_requested():
            self.store.request_cancel(run_id)

        run = self.store.get_run(run_id)
        if is_terminal(run.status):
            return run, False

        stage = self._next_ready_stage(run_id)
        if stage is None:
            return run, False

        manifest = self.resolver.resolve(
            stage.plugin_id, version=stage.plugin_version,
            arch=platform.machine(),
        )
        previous = self.store.list_attempts(stage.stage_run_id)
        attempt_number = len(previous) + 1
        if manifest.requirements.resumable and previous:
            # Continue in the workspace the last attempt left behind.  A fresh
            # empty directory would be the opposite of resuming: a flow that
            # resumes by re-running its own makefile needs the results it
            # already has, and those are here.
            workspace = Path(previous[-1].workspace)
        else:
            workspace = (
                self.config.workspace_root / run_id / stage.stage_run_id
                / f"attempt-{attempt_number}"
            )
        try:
            reservation = self.config.reservation_for(run.task_spec)
            attempt = self.store.start_attempt(
                stage.stage_run_id, worker_id=self.config.worker_id,
                workspace=workspace, lease_seconds=self.config.lease_seconds,
                resources=reservation,
                capacity_cpu_cores=self.config.capacity_cpu_cores,
                capacity_memory_bytes=self.config.capacity_memory_bytes,
                platform_fraction=self.config.platform_fraction,
            )
            if attempt is None:
                return self.store.get_run(run_id), False
            workspace.mkdir(parents=True, exist_ok=True)
        except InvalidTransition:
            # Another worker won the race between selection and claim.  Return
            # the authoritative state, and report that we did nothing.
            return self.store.get_run(run_id), False

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
        except Exception as exc:  # noqa: BLE001 - terminalize runtime failures
            self._record_runtime_failure(run, attempt, exc)
        finally:
            observer.record_summary()
        return self.store.get_run(run_id), True

    def _run_attempt(
        self, run: RunRecord, stage: StageRun, attempt: Attempt,
        manifest: PluginManifest, workspace: Path,
        pulse: _LeasePulse, observer: ProgressObserver,
    ) -> None:
        environment = dict(
            self.environment_resolver(run) if self.environment_resolver else {}
        )
        # Staged before anything else runs, and recorded before the adapter
        # starts: what an attempt was given is evidence even if the adapter
        # then crashes, and a failed attempt that read the wrong design is
        # exactly the case where that evidence matters most.
        staged_inputs, input_manifest = self._stage_inputs(
            run.task_spec, workspace
        )
        self.store.record_inputs(attempt.attempt_id, staged_inputs)
        receipt = self._write_protocol_receipt(
            manifest, run, attempt, workspace, environment
        )

        execution = self.adapter.execute(
            manifest, run.task_spec, workspace=workspace,
            cancel_requested=pulse, on_line=observer, environment=environment,
            limits=run.task_spec.resources,
        )
        self._reject_forged_authority(execution)

        for written in (receipt, input_manifest):
            if written is not None and written["sha256"] != sha256(written["path"]):
                raise RuntimeStoreError(
                    f"adapter modified the platform's own "
                    f"{written['declaration']['kind']}"
                )

        # The receipt and the input manifest are the platform's own
        # bookkeeping, so they are the only declarations allowed to carry a
        # reserved kind.
        runtime_artifacts = validate_artifact_declarations(
            workspace, manifest,
            tuple(
                written["declaration"]
                for written in (input_manifest, receipt)
                if written is not None
            ),
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
            cpu_seconds=execution.outcome.cpu_seconds,
            peak_memory_bytes=execution.outcome.peak_memory_bytes,
            peak_processes=execution.outcome.peak_processes,
        )
        if self._should_retry(run, attempt, execution):
            self.store.schedule_retry(
                run.run_id, stage.stage_run_id,
                reason=f"retrying: {_terminal_reason(execution.result)}",
            )
            return
        self.store.transition_run(
            run.run_id, execution.result.status,
            reason=_terminal_reason(execution.result),
        )

    @staticmethod
    def _should_retry(
        run: RunRecord, attempt: Attempt, execution: AdapterExecution,
    ) -> bool:
        """Whether a failed attempt has another attempt left in its budget.

        Who decides what may be retried?  The plugin, in its own failure report:
        it is the only party that knows whether trying again could help -- a
        missing tool will not appear, a transient read might succeed.  The
        platform decides only the budget, because the budget is lifecycle, and
        because an unbounded retry loop is the platform's problem to prevent.

        Nothing else is retried: a timeout, a cancellation and a lost lease are
        not failures the plugin asked to repeat, and a protocol error is a plugin
        bug that a second run would reproduce.
        """
        if execution.result.status is not RuntimeStatus.FAILED:
            return False
        failure = execution.result.failure or {}
        if not failure.get("retryable"):
            return False
        return attempt.attempt_number < run.task_spec.max_attempts

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

        Which file is read comes from the artifact's own record, not from an
        assumption about where artifacts live.  An artifact registered before
        the object store existed is still in its attempt workspace, and saying
        so is the store's job.
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
        # The search above is a membership check: a caller authorised for this
        # run must not be able to read another run's artifact by quoting its id.
        _, artifact = matches[0]
        path = self.store.artifact_path(artifact_id)
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
