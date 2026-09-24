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

import base64
import json
import math
import os
import platform
import shutil
import socket
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol, cast

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
from .worker_presence import touch as touch_worker


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
        self,
        plugin_id: str,
        *,
        version: str | None = None,
        capability: str | None = None,
        arch: str | None = None,
    ) -> PluginManifest: ...  # pragma: no cover - protocol definition


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
    #: Ordinary EDA runs receive a six-gigabyte working budget. Larger jobs
    #: must carry an explicit manual approval label.
    default_task_memory_bytes: int = 6 << 30
    #: Absolute host paths accepted as caller-provided inputs.  An empty tuple
    #: preserves the trusted local-library mode; shared deployments must set a
    #: bounded root and use uploaded inputs for everything else.
    allowed_input_roots: tuple[Path, ...] = ()

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
            cpu_seconds=(requested.cpu_seconds if requested else None),
            cpu_cores=(requested.cpu_cores if requested and requested.cpu_cores is not None else self.default_task_cpu_cores),
            memory_bytes=(requested.memory_bytes if requested and requested.memory_bytes is not None else self.default_task_memory_bytes),
            processes=(requested.processes if requested else None),
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
        self.allowed_input_roots = tuple(Path(root).expanduser().resolve() for root in self.allowed_input_roots)
        if not self.worker_id:
            self.worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


#: Environment variable carrying the immutable protocol receipt to an adapter
#: is declared by the manifest, not concatenated by the kernel.
RECEIPT_ARTIFACT_KIND = "runtime_protocol_receipt"

# Files that explain a failed attempt even when the adapter did not produce a
# domain artifact.  They are platform evidence, not plugin claims: the store
# hashes the bytes after the process has stopped.
FAILURE_EVIDENCE_FILES = (
    ("adapter_request.json", "runtime_evidence_request"),
    ("adapter_result.json", "runtime_evidence_result"),
    ("adapter.log", "runtime_evidence_log"),
    (INPUT_MANIFEST_FILENAME, "runtime_evidence_input_manifest"),
    ("runtime_protocol_receipt.json", "runtime_evidence_protocol_receipt"),
)


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
                lease_seconds=lease_seconds,
                worker_id=worker_id,
            )
        self.store = store
        self.resolver = resolver
        self.config = config
        self.adapter = adapter or ProcessAdapter()
        self.protected_evaluator = protected_evaluator
        self.environment_resolver = environment_resolver

    # -- submission -------------------------------------------------------

    def submit(
        self,
        task: TaskSpec,
        *,
        plugin_version: str | None = None,
        capability: str | None = None,
        idempotent: bool = False,
        idempotency_key: str | None = None,
    ) -> RunRecord:
        task.validate()
        self._check_inputs(task)
        task = self._freeze_host_inputs(task)
        task = replace(
            task,
            input_manifest_sha256=self._input_manifest_digest(task),
        )
        self._check_resources(task)
        if task.plugin_id is None:
            raise ValueError("this runtime executes direct plugin tasks only")
        if plugin_version and task.plugin_version and plugin_version != task.plugin_version:
            raise ValueError("plugin_version argument conflicts with the task plugin_version")
        requested_version = plugin_version or task.plugin_version
        manifest = self.resolver.resolve(
            task.plugin_id,
            version=requested_version,
            capability=capability,
            arch=platform.machine(),
        )
        return self.store.submit_run(
            task,
            stage_key="main",
            plugin_version=manifest.plugin_version,
            idempotent=idempotent,
            idempotency_key=idempotency_key,
            resumable=manifest.requirements.resumable,
        )

    def _input_manifest_digest(self, task: TaskSpec) -> str:
        """Hash the bytes and destinations that define this run's input base."""
        entries: list[dict[str, Any]] = []
        for declaration in task.staged_inputs:
            if declaration.input_id is not None:
                uploaded = self.store.get_uploaded_input(declaration.input_id)
                digest, size = uploaded.sha256, uploaded.size_bytes
            elif declaration.artifact_id is not None:
                artifact = self.store.get_artifact(declaration.artifact_id)
                try:
                    present = self.store.artifact_path(declaration.artifact_id).is_file()
                except RuntimeStoreError:
                    present = False
                if not present and not declaration.required:
                    digest, size = None, 0
                else:
                    digest, size = artifact.sha256, artifact.size_bytes
            else:
                source = Path(cast(str, declaration.source)).expanduser().resolve()
                if not source.is_file():
                    if declaration.required:
                        raise InputStagingError(f"required input is not a readable file: {source!s}")
                    digest, size = None, 0
                else:
                    digest, size = sha256(source), source.stat().st_size
            entries.append(
                {
                    "destination": declaration.destination,
                    "present": digest is not None,
                    "sha256": digest,
                    "size_bytes": size,
                }
            )
        payload = json.dumps(entries, sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode("utf-8"))

    def _freeze_host_inputs(self, task: TaskSpec) -> TaskSpec:
        frozen: list[InputFile] = []
        labels = dict(task.labels)
        changed = False
        for index, declaration in enumerate(task.staged_inputs):
            if declaration.source is None:
                frozen.append(declaration)
                continue
            source = Path(declaration.source).expanduser().resolve()
            if not source.is_file():
                frozen.append(declaration)
                continue
            uploaded = self.store.ingest_input_file(source)
            labels[f"__platform_host_source_{index}"] = str(source)
            frozen.append(
                InputFile(
                    destination=declaration.destination,
                    input_id=uploaded.input_id,
                    required=declaration.required,
                )
            )
            changed = True
        return replace(task, staged_inputs=tuple(frozen), labels=labels) if changed else task

    def submit_idempotent(
        self,
        task: TaskSpec,
        *,
        plugin_version: str | None = None,
        capability: str | None = None,
        idempotency_key: str | None = None,
    ) -> RunRecord:
        """Submit once by explicit key, or by task id when no key is supplied."""
        return self.submit(
            task,
            plugin_version=plugin_version,
            capability=capability,
            idempotent=True,
            idempotency_key=idempotency_key,
        )

    # -- inputs -----------------------------------------------------------

    def _check_resources(self, task: TaskSpec) -> None:
        """Refuse a task whose declared limits this host cannot enforce."""
        resources = self.config.reservation_for(task)
        budget_cpu = max(1, math.ceil(self.config.capacity_cpu_cores * self.config.platform_fraction))
        budget_memory = max(
            1,
            math.ceil(self.config.capacity_memory_bytes * self.config.platform_fraction),
        )
        if budget_cpu < 1 or budget_memory < self.config.default_task_memory_bytes:
            raise ValueError("platform capacity is smaller than the default task reservation")
        if resources.cpu_cores is not None and resources.cpu_cores > budget_cpu:
            raise ValueError("requested cpu_cores exceed platform budget")
        if resources.memory_bytes is not None and resources.memory_bytes > budget_memory:
            raise ValueError("requested memory exceeds platform budget")
        if (resources.memory_bytes is not None
                and resources.memory_bytes > self.config.default_task_memory_bytes
                and task.labels.get("memory_approval") != "manual"):
            raise ValueError(
                "memory requests above 6 GiB require labels.memory_approval='manual'"
            )
        if task.resources is not None and task.resources.declared and not self.adapter.supports_limits():
            raise ResourceLimitsUnsupported(f"this host cannot measure a process tree, so the requested limits ({resources.describe()}) cannot be enforced")

    def _check_inputs(self, task: TaskSpec) -> None:
        for index, declaration in enumerate(task.staged_inputs):
            if declaration.input_id is not None:
                try:
                    self.store.get_uploaded_input(declaration.input_id)
                except RuntimeStoreError as exc:
                    raise InputStagingError(f"input references an uploaded input the platform does not have: {declaration.input_id!r}") from exc
                continue
            if declaration.artifact_id is not None:
                try:
                    self.store.get_artifact(declaration.artifact_id)
                except RuntimeStoreError as exc:
                    raise InputStagingError(f"input references an artifact the platform does not have: {declaration.artifact_id!r}") from exc
                continue
            source = Path(cast(str, declaration.source)).expanduser().resolve()
            if self.config.allowed_input_roots and not any(_within(source, root) for root in self.config.allowed_input_roots):
                raise InputStagingError(f"host input is outside configured input roots: {declaration.source!r}")
            if declaration.required and not source.is_file():
                raise InputStagingError(f"required input is not a readable file: {declaration.source!r} (declared as {declaration.destination!r})")

    def _stage_inputs(self, task: TaskSpec, workspace: Path) -> tuple[tuple[StagedInput, ...], dict[str, Any] | None]:
        if not task.staged_inputs:
            return (), None

        staged: list[StagedInput] = []
        for index, declaration in enumerate(task.staged_inputs):
            destination = workspace / declaration.destination
            if declaration.input_id is not None:
                staged.append(self._stage_from_input(
                    declaration, destination,
                    source=task.labels.get(f"__platform_host_source_{index}"),
                ))
                continue
            if declaration.artifact_id is not None:
                staged.append(self._stage_from_artifact(declaration, destination))
                continue
            source = Path(cast(str, declaration.source)).expanduser().resolve()
            if not source.is_file():
                if declaration.required:
                    raise InputStagingError(f"required input disappeared before it could be staged: {declaration.source!r}")
                staged.append(
                    StagedInput(
                        destination=declaration.destination,
                        source=declaration.source,
                        present=False,
                        size_bytes=0,
                    )
                )
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            staged.append(
                StagedInput(
                    destination=declaration.destination,
                    source=declaration.source,
                    present=True,
                    size_bytes=destination.stat().st_size,
                    sha256=sha256(destination),
                )
            )

        manifest_path = workspace / INPUT_MANIFEST_FILENAME
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    # An identity document, not an audit record.  Where a file happened
                    # to live is not part of what it is: two runs that read the same
                    # bytes into the same destinations are running the same design even
                    # if one caller kept it under /tmp and the other on a shared volume.
                    # Including the source would make those two manifests differ, and
                    # the digest would then measure the caller's filing habits.  The
                    # source is recorded in ``runtime_inputs``, where it is provenance.
                    "inputs": [
                        {
                            "destination": item.destination,
                            "present": item.present,
                            "size_bytes": item.size_bytes,
                            "sha256": item.sha256,
                        }
                        for item in staged
                    ],
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return tuple(staged), {
            "path": manifest_path,
            "sha256": sha256(manifest_path),
            "declaration": {
                "kind": INPUT_MANIFEST_KIND,
                "path": manifest_path.name,
                "metadata": {"producer": "runtime"},
            },
        }

    def _stage_from_artifact(self, declaration: InputFile, destination: Path) -> StagedInput:
        """Place bytes the platform already holds, and check them as they land.

        The copy is verified against the artifact's own record rather than
        trusted because it came from the object store.  That check is the whole
        reason a reference is worth having: a run that says "this came from
        artifact X" becomes a claim someone can falsify, and this is where it is
        checked.
        """
        try:
            artifact = self.store.materialize_artifact(cast(str, declaration.artifact_id), destination)
        except RuntimeStoreError as exc:
            if declaration.required:
                raise InputStagingError(str(exc)) from exc
            return StagedInput(
                destination=declaration.destination,
                present=False,
                size_bytes=0,
                source_artifact_id=declaration.artifact_id,
            )
        digest = sha256(destination)
        size_bytes = destination.stat().st_size
        if digest != artifact.sha256 or size_bytes != artifact.size_bytes:
            raise InputStagingError(
                f"artifact {declaration.artifact_id!r} does not match its own record: recorded {artifact.sha256} ({artifact.size_bytes} bytes), copied {digest} ({size_bytes} bytes)"
            )
        return StagedInput(
            destination=declaration.destination,
            present=True,
            size_bytes=size_bytes,
            sha256=digest,
            source_artifact_id=declaration.artifact_id,
        )

    def _stage_from_input(self, declaration: InputFile, destination: Path,
                          *, source: str | None = None) -> StagedInput:
        try:
            uploaded = self.store.materialize_input(cast(str, declaration.input_id), destination)
        except RuntimeStoreError as exc:
            if declaration.required:
                raise InputStagingError(str(exc)) from exc
            return StagedInput(
                destination=declaration.destination,
                present=False,
                size_bytes=0,
                source_input_id=declaration.input_id,
            )
        digest = sha256(destination)
        size_bytes = destination.stat().st_size
        if digest != uploaded.sha256 or size_bytes != uploaded.size_bytes:
            raise InputStagingError(f"uploaded input {declaration.input_id!r} does not match its record: {uploaded.sha256} ({uploaded.size_bytes}), copied {digest} ({size_bytes})")
        return StagedInput(
            destination=declaration.destination,
            present=True,
            size_bytes=size_bytes,
            sha256=digest,
            source=source,
            source_input_id=declaration.input_id,
        )

    # -- execution --------------------------------------------------------

    def execute_once(
        self,
        run_id: str,
        *,
        on_line: Callable[[str], None] | None = None,
        external_cancel_requested: Callable[[], bool] | None = None,
    ) -> RunRecord:
        """Advance a run by at most one attempt."""
        run, _ = self.execute_once_reporting(
            run_id,
            on_line=on_line,
            external_cancel_requested=external_cancel_requested,
        )
        return run

    def execute_once_reporting(
        self,
        run_id: str,
        *,
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
            stage.plugin_id,
            version=stage.plugin_version,
            arch=platform.machine(),
        )
        previous = self.store.list_attempts(stage.stage_run_id)
        attempt_number = len(previous) + 1
        if manifest.requirements.resumable and previous:
            workspace = Path(previous[-1].workspace)
        else:
            workspace = self.config.workspace_root / run_id / stage.stage_run_id / f"attempt-{attempt_number}"
        try:
            reservation = self.config.reservation_for(run.task_spec)
            attempt = self.store.start_attempt(
                stage.stage_run_id,
                worker_id=self.config.worker_id,
                workspace=workspace,
                lease_seconds=self.config.lease_seconds,
                resources=reservation,
                capacity_cpu_cores=self.config.capacity_cpu_cores,
                capacity_memory_bytes=self.config.capacity_memory_bytes,
                platform_fraction=self.config.platform_fraction,
            )
            if attempt is None:
                return self.store.get_run(run_id), False
            touch_worker(self.store, self.config.worker_id, attempt_id=attempt.attempt_id)
            try:
                workspace.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self._record_runtime_failure(run, attempt, exc)
                return self.store.get_run(run_id), True
        except InvalidTransition:
            return self.store.get_run(run_id), False

        pulse = _LeasePulse(
            self.store,
            run_id,
            attempt.attempt_id,
            worker_id=self.config.worker_id,
            lease_seconds=self.config.lease_seconds,
            external_cancel_requested=external_cancel_requested,
        )
        observer = ProgressObserver(
            self.store,
            run_id=run_id,
            stage_run_id=stage.stage_run_id,
            attempt_id=attempt.attempt_id,
            marker=manifest.progress_marker,
            producer=f"adapter:{manifest.plugin_id}@{manifest.plugin_version}",
            downstream=on_line,
        )

        try:
            self._run_attempt(
                run,
                stage,
                attempt,
                manifest,
                workspace,
                pulse,
                observer,
            )
        except Exception as exc:  # noqa: BLE001 - terminalize runtime failures
            self._record_runtime_failure(run, attempt, exc)
        finally:
            observer.record_summary()
            touch_worker(self.store, self.config.worker_id)
        return self.store.get_run(run_id), True

    def _run_attempt(
        self,
        run: RunRecord,
        stage: StageRun,
        attempt: Attempt,
        manifest: PluginManifest,
        workspace: Path,
        pulse: _LeasePulse,
        observer: ProgressObserver,
    ) -> None:
        environment = dict(self.environment_resolver(run) if self.environment_resolver else {})
        # Staged before anything else runs, and recorded before the adapter
        # starts: what an attempt was given is evidence even if the adapter
        # then crashes, and a failed attempt that read the wrong design is
        # exactly the case where that evidence matters most.
        staged_inputs, input_manifest = self._stage_inputs(run.task_spec, workspace)
        self.store.record_inputs(attempt.attempt_id, staged_inputs)
        self._verify_input_manifest(run.task_spec, staged_inputs)
        receipt = self._write_protocol_receipt(manifest, run, attempt, workspace, environment)

        # Admission and enforcement must use the same effective request.  A
        # task that omits resources still reserves the configured default, so
        # passing the raw optional field here would make the scheduler's
        # capacity story purely accounting: the process could use more than
        # the reservation it consumed.
        execution = self.adapter.execute(
            manifest,
            run.task_spec,
            workspace=workspace,
            cancel_requested=pulse,
            on_line=observer,
            environment=environment,
            limits=(self.config.reservation_for(run.task_spec) if self.adapter.supports_limits() else run.task_spec.resources),
            on_started=lambda pid, pgid, ticks: self.store.attach_process(
                attempt.attempt_id,
                worker_id=self.config.worker_id,
                process_id=pid,
                process_group_id=pgid,
                process_start_ticks=ticks,
            ),
        )
        self._reject_forged_authority(execution)
        # Keep the small platform evidence files outside the mutable workspace
        # so a later bundle export remains possible after workspace cleanup.
        for filename in (
            "adapter_request.json",
            "adapter_result.json",
            "adapter.log",
            INPUT_MANIFEST_FILENAME,
            "runtime_protocol_receipt.json",
        ):
            self.store.snapshot_evidence(attempt.attempt_id, workspace, filename)

        for written in (receipt, input_manifest):
            if written is not None and written["sha256"] != sha256(written["path"]):
                raise RuntimeStoreError(f"adapter modified the platform's own {written['declaration']['kind']}")

        # The receipt and the input manifest are the platform's own
        # bookkeeping, so they are the only declarations allowed to carry a
        # reserved kind.
        runtime_artifacts = validate_artifact_declarations(
            workspace,
            manifest,
            tuple(written["declaration"] for written in (input_manifest, receipt) if written is not None),
            expected_kinds=(),
            require_expected=False,
            allow_reserved=True,
        )
        evaluator_artifacts, evaluator_metrics = self._evaluate(execution, manifest, run, workspace, attempt.attempt_id)
        occupied = {str(item["store_key"]) for item in (*runtime_artifacts, *execution.artifacts, *evaluator_artifacts)}
        collected = self._collect_manifest_artifacts(workspace, manifest, occupied)
        registered = (*runtime_artifacts, *execution.artifacts, *evaluator_artifacts, *collected)
        registered_ids = self.store.register_artifacts(attempt.attempt_id, workspace, registered)
        if execution.result.status is not RuntimeStatus.SUCCEEDED:
            evidence_error = self._register_failure_evidence(attempt)
            if evidence_error is not None:
                raise RuntimeStoreError(f"could not preserve failure evidence: {evidence_error}")
        if execution.result.status is RuntimeStatus.SUCCEEDED:
            self._register_metrics(
                attempt,
                (*execution.result.metrics, *evaluator_metrics),
                registered,
                registered_ids,
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
                run.run_id,
                stage.stage_run_id,
                reason=f"retrying: {_terminal_reason(execution.result)}",
            )
            return
        self.store.transition_run(
            run.run_id,
            execution.result.status,
            reason=_terminal_reason(execution.result),
        )

    @staticmethod
    def _collect_manifest_artifacts(workspace: Path, manifest: PluginManifest,
                                    occupied: set[str]) -> tuple[dict[str, Any], ...]:
        """Collect files a Toolkit explicitly marks as platform custody."""
        collected: list[dict[str, Any]] = []
        for rule in manifest.artifact_rules:
            patterns = rule.get("patterns", rule.get("pattern"))
            if patterns is None or not rule.get("collect", True):
                continue
            if isinstance(patterns, str):
                patterns = (patterns,)
            if not isinstance(patterns, (list, tuple)):
                raise RuntimeStoreError("artifact collection patterns must be strings")
            matches = {path.resolve() for pattern in patterns for path in workspace.glob(str(pattern)) if path.is_file()}
            if not matches and rule.get("required"):
                raise RuntimeStoreError(f"required artifact collection matched nothing: {patterns!r}")
            for path in sorted(matches):
                try:
                    relative = path.relative_to(workspace).as_posix()
                except ValueError as exc:
                    raise RuntimeStoreError("artifact collection escaped workspace") from exc
                if relative in occupied:
                    continue
                occupied.add(relative)
                collected.append({"kind": str(rule["kind"]), "store_key": relative,
                                  "metadata": {"producer": "toolkit_manifest", "collected": True}})
        return tuple(collected)

    @staticmethod
    def _should_retry(
        run: RunRecord,
        attempt: Attempt,
        execution: AdapterExecution,
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
        self,
        manifest: PluginManifest,
        run: RunRecord,
        attempt: Attempt,
        workspace: Path,
        environment: dict[str, str],
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
            raise RuntimeStoreError(f"{manifest.plugin_id!r} requires an immutable experiment_protocol in inputs")
        path = workspace / "runtime_protocol_receipt.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "protocol": protocol,
                    "run_id": run.run_id,
                    "attempt_id": attempt.attempt_id,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

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
                "metadata": {"producer": "runtime", "attempt_id": attempt.attempt_id},
            },
        }

    @staticmethod
    def _reject_forged_authority(execution: AdapterExecution) -> None:
        """An adapter may not manufacture the kernel's or evaluator's authority."""
        for item in execution.result.artifacts:
            if item.get("kind") == RECEIPT_ARTIFACT_KIND:
                raise RuntimeStoreError(f"adapter declared runtime-reserved artifact kind {RECEIPT_ARTIFACT_KIND!r}")
            metadata = item.get("metadata") or {}
            if metadata.get("official_qor") is not None:
                raise RuntimeStoreError("adapter declared a metric the platform reserves for the protected evaluator")
            if str(metadata.get("producer", "")).startswith("protected-"):
                raise RuntimeStoreError("adapter claimed a protected-evaluator producer identity")

    def _evaluate(
        self,
        execution: AdapterExecution,
        manifest: PluginManifest,
        run: RunRecord,
        workspace: Path,
        attempt_id: str,
    ) -> tuple[tuple[dict[str, Any], ...], tuple[Metric, ...]]:
        if execution.result.status is not RuntimeStatus.SUCCEEDED:
            return (), ()
        if self.protected_evaluator is None:
            if manifest.requirements.require_protected_evaluation:
                raise RuntimeStoreError("protected evaluator is unavailable")
            return (), ()
        verdict: Verdict = self.protected_evaluator.evaluate(
            EvaluationRequest(
                manifest=manifest,
                task=run.task_spec,
                workspace=str(workspace),
                attempt_id=attempt_id,
                declared_artifacts=(),
            )
        )
        verdict.validate()
        if verdict.status is VerdictStatus.REJECTED:
            raise RuntimeStoreError(f"protected evaluator rejected the run: {verdict.reason}")
        artifacts = validate_artifact_declarations(
            workspace,
            manifest,
            [{"kind": a.kind, "path": a.path, "metadata": a.metadata} for a in verdict.artifacts],
            expected_kinds=(),
            require_expected=False,
        )
        return artifacts, verdict.metrics

    def _register_metrics(
        self,
        attempt: Attempt,
        raw_metrics: tuple[dict[str, Any] | Metric, ...],
        registered: tuple[dict[str, Any], ...],
        registered_ids: list[str],
    ) -> None:
        """Attach each metric to the artifact it was read from.

        A metric whose source cannot be resolved is a protocol error: the
        platform will not store a number it cannot trace.
        """
        by_store_key = {item["store_key"]: artifact_id for item, artifact_id in zip(registered, registered_ids)}
        metrics = []
        for raw in raw_metrics:
            item = raw.to_dict() if isinstance(raw, Metric) else dict(raw)
            context = dict(item.get("context") or {})
            source_key = context.pop("source_artifact_store_key", None)
            if source_key is not None:
                artifact_id = by_store_key.get(source_key)
                if artifact_id is None:
                    raise RuntimeStoreError(f"metric {item.get('name')!r} references an unregistered artifact {source_key!r}")
                item["source_artifact_id"] = artifact_id
            item["parser_id"] = context.pop("parser_id", None)
            item["parser_version"] = context.pop("parser_version", None)
            item["context"] = context
            item.pop("source_artifact_store_key", None)
            metrics.append(Metric(**item))
        self.store.register_metrics(attempt.attempt_id, metrics)

    def _record_runtime_failure(self, run: RunRecord, attempt: Attempt, exc: Exception) -> None:
        """Terminate both the attempt and the run.

        Failing only the attempt leaves the run stuck in RUNNING forever, which
        is worse than a wrong answer: it is a run nothing will ever finish and
        nobody will ever see fail.
        """
        failure = {
            "category": "runtime_error",
            "message": f"{type(exc).__name__}: {exc}",
        }
        evidence_error = self._register_failure_evidence(attempt)
        if evidence_error is not None:
            failure["evidence_error"] = evidence_error
        try:
            self.store.finish_attempt(
                attempt.attempt_id,
                AttemptStatus.FAILED,
                exit_code=1,
                failure=failure,
            )
        except InvalidTransition:
            # A lease monitor may already have moved RUNNING to LOST.  LOST is
            # authoritative evidence; do not overwrite it and do not crash.
            pass
        try:
            current = self.store.get_run(run.run_id)
            if current.status is RuntimeStatus.RUNNING:
                self.store.transition_run(
                    run.run_id,
                    RuntimeStatus.FAILED,
                    reason=failure["category"],
                )
        except InvalidTransition:
            pass

    def _register_failure_evidence(self, attempt: Attempt) -> str | None:
        declarations = []
        workspace = Path(attempt.workspace)
        existing = {artifact.store_key for artifact in self.store.list_artifacts(attempt.attempt_id)}
        for filename, kind in FAILURE_EVIDENCE_FILES:
            if filename not in existing and (workspace / filename).is_file():
                declarations.append(
                    {
                        "kind": kind,
                        "store_key": filename,
                        "metadata": {"producer": "runtime", "failure_evidence": True},
                    }
                )
        if not declarations:
            return None
        try:
            self.store.register_artifacts(
                attempt.attempt_id,
                workspace,
                declarations,
            )
        except (OSError, RuntimeStoreError, ValueError) as evidence_exc:
            return f"{type(evidence_exc).__name__}: {evidence_exc}"
        return None

    def _verify_input_manifest(self, task: TaskSpec, staged: tuple[StagedInput, ...]) -> None:
        entries = [
            {
                "destination": item.destination,
                "present": item.present,
                "sha256": item.sha256,
                "size_bytes": item.size_bytes,
            }
            for item in staged
        ]
        digest = sha256(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        if task.input_manifest_sha256 and digest != task.input_manifest_sha256:
            raise InputStagingError("input bytes changed after submission; execution was refused")

    # -- reads ------------------------------------------------------------

    def describe(self, run_id: str) -> dict[str, Any]:
        return self.store.describe_run(run_id)

    def read_artifact_excerpt(
        self,
        run_id: str,
        artifact_id: str,
        *,
        offset: int,
        max_bytes: int,
        verify_hash: bool = True,
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
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0 or not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 0 < max_bytes <= 64 * 1024:
            raise ValueError("artifact excerpt bounds are invalid")
        view = self.describe(run_id)
        matches = [
            (attempt, artifact) for stage in view.get("stages", ()) for attempt in stage.get("attempts", ()) for artifact in attempt.get("artifacts", ()) if artifact.get("artifact_id") == artifact_id
        ]
        if len(matches) != 1:
            raise RuntimeStoreError(f"artifact {artifact_id!r} is not registered in run {run_id!r}")
        # The search above is a membership check: a caller authorised for this
        # run must not be able to read another run's artifact by quoting its id.
        _, artifact = matches[0]
        path = self.store.artifact_path(artifact_id)
        if verify_hash and sha256(path) != artifact["sha256"]:
            raise RuntimeStoreError(f"registered artifact {artifact_id!r} changed after registration")
        with path.open("rb") as source:
            source.seek(offset)
            chunk = source.read(max_bytes)
        return {
            "artifact_id": artifact_id,
            "sha256": str(artifact["sha256"]),
            "offset": offset,
            "text": chunk.decode("utf-8", errors="replace"),
            "data_base64": base64.b64encode(chunk).decode("ascii"),
            "bytes_read": len(chunk),
            "size_bytes": artifact["size_bytes"],
            "truncated": offset + len(chunk) < artifact["size_bytes"],
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
        self,
        store: RuntimeStore,
        run_id: str,
        attempt_id: str,
        *,
        worker_id: str,
        lease_seconds: int,
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
        if self.external_cancel_requested is not None and self.external_cancel_requested():
            self.store.request_cancel(self.run_id)
            return True
        if self.store.get_run(self.run_id).status is RuntimeStatus.CANCEL_REQUESTED:
            return True
        now = time.monotonic()
        if now - self._last >= max(1.0, self.lease_seconds / 3):
            self.store.heartbeat(
                self.attempt_id,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )
            touch_worker(self.store, self.worker_id, attempt_id=self.attempt_id)
            self._last = now
        return False


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


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
