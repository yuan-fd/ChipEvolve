"""Runtime orchestration: leases, validation, evidence, evaluation.

The interesting assertions here are the ones about what the runtime *refuses*.
A runtime that records whatever it is told is not an authority, and every QoR
number stored through it becomes worthless.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from openroad_platform_contracts import (
    ArtifactDeclaration,
    AttemptStatus,
    EvaluationRequest,
    Metric,
    PluginManifest,
    RuntimeRequirements,
    RuntimeStatus,
    TaskSpec,
    is_terminal,
    Verdict,
    VerdictStatus,
)
from openroad_platform_runtime import (
    InvalidTransition,
    RuntimeStore,
    RuntimeStoreError,
    WorkflowRuntime,
)
from openroad_platform_runtime.guardian import ProcessGuardian
from openroad_platform_runtime.adapter import ProcessAdapter

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_adapter.py"


class Resolver:
    """A stand-in registry. The runtime only needs resolve()."""

    def __init__(self, manifest: PluginManifest):
        self.manifest = manifest
        self.calls: list[tuple[str, str | None]] = []

    def resolve(self, plugin_id, *, version=None, capability=None, arch=None):
        self.calls.append((plugin_id, version))
        if plugin_id != self.manifest.plugin_id:
            raise LookupError(plugin_id)
        if version is not None and version != self.manifest.plugin_version:
            raise LookupError(version)
        return self.manifest


def manifest(**overrides) -> PluginManifest:
    base = dict(
        plugin_id="fake-capability",
        plugin_version="1.0.0",
        adapter_entry=(sys.executable, str(FIXTURE)),
        capabilities=("do.thing",),
        supported_arch=("aarch64", "x86_64", "arm64"),
        artifact_rules=(
            {"kind": "report", "required": True},
            {"kind": "log", "required": False},
        ),
        default_timeout_seconds=60,
    )
    base.update(overrides)
    return PluginManifest(**base)


def task(behaviour: str, **overrides) -> TaskSpec:
    base = dict(
        task_id=f"task-{behaviour}", project_id="p", design_id="d",
        plugin_id="fake-capability",
        inputs={"behaviour": behaviour},
        timeout_seconds=30,
    )
    base.update(overrides)
    return TaskSpec(**base)


def runtime(tmp_path: Path, manifest_obj: PluginManifest, **kwargs) -> WorkflowRuntime:
    store = kwargs.pop("store", None) or RuntimeStore(tmp_path / "runtime.db")
    return WorkflowRuntime(
        store, Resolver(manifest_obj),
        workspace_root=tmp_path / "ws",
        adapter=ProcessAdapter(
            ProcessGuardian(poll_interval=0.02, terminate_grace=1.0)
        ),
        lease_seconds=30,
        worker_id="test-worker",
        **kwargs,
    )


# --------------------------------------------------------------------------
# submission
# --------------------------------------------------------------------------

def test_submit_resolves_the_manifest_and_creates_a_queued_run(tmp_path):
    rt = runtime(tmp_path, manifest())
    run = rt.submit(task("ok"))
    assert run.status is RuntimeStatus.QUEUED
    assert run.task_spec.task_id == "task-ok"


def test_submit_uses_the_registry_version_not_the_callers(tmp_path):
    resolver = Resolver(manifest())
    rt = WorkflowRuntime(RuntimeStore(tmp_path / "r.db"), resolver,
                         workspace_root=tmp_path / "ws")
    run = rt.submit(task("ok"))
    stage = rt.store.list_stages(run.run_id)[0]
    assert stage.plugin_version == "1.0.0"


def test_resubmitting_an_identical_task_is_idempotent(tmp_path):
    rt = runtime(tmp_path, manifest())
    first = rt.submit_idempotent(task("ok"))
    second = rt.submit_idempotent(task("ok"))
    assert first.run_id == second.run_id


def test_resubmitting_a_different_spec_under_the_same_id_is_refused(tmp_path):
    rt = runtime(tmp_path, manifest())
    rt.submit_idempotent(task("ok"))
    with pytest.raises(RuntimeStoreError, match="different"):
        rt.submit_idempotent(task("ok", inputs={"behaviour": "fail"}))


# --------------------------------------------------------------------------
# the happy path end to end
# --------------------------------------------------------------------------

def test_a_successful_attempt_registers_artifacts_and_links_metrics(tmp_path):
    rt = runtime(tmp_path, manifest())
    run = rt.submit(task("ok"))
    finished = rt.execute_once(run.run_id)

    assert finished.status is RuntimeStatus.SUCCEEDED
    view = rt.describe(run.run_id)
    attempt = view["stages"][0]["attempts"][0]
    assert {a["kind"] for a in attempt["artifacts"]} == {"report", "log"}

    # The metric must point at the artifact it came from: an unsourced number
    # is a display value, not evidence.
    metric = attempt["metrics"][0]
    assert metric["name"] == "area_um2"
    assert metric["value"] == 1234.5
    artifact_ids = {a["artifact_id"] for a in attempt["artifacts"]}
    assert metric["source_artifact_id"] in artifact_ids
    assert metric["parser_id"] == "fake"


def test_stage_progress_becomes_durable_events_without_the_kernel_knowing_stages(
    tmp_path,
):
    rt = runtime(tmp_path, manifest())
    run = rt.submit(task("ok"))
    rt.execute_once(run.run_id)

    events = [e for e in rt.store.list_events(run.run_id)
              if e.event_type.startswith("stage.")]
    started = [e for e in events if e.event_type == "stage.started"]
    finished = [e for e in events if e.event_type == "stage.finished"]

    # The stage names come from the fake plugin; the kernel only knows the
    # envelope. Nothing here is hardcoded in the platform.
    assert [e.payload["stage"] for e in started] == ["alpha", "beta"]
    assert [e.payload["stage"] for e in finished] == ["alpha", "beta"]
    assert finished[0].payload["status"] == "succeeded"
    assert finished[0].payload["seconds"] == 1.5


def test_a_custom_progress_marker_is_honoured(tmp_path):
    """A plugin may choose its own marker; the kernel reads it from the manifest."""
    rt = runtime(tmp_path, manifest(progress_marker="[progress]"))
    run = rt.submit(task("ok"))
    rt.execute_once(run.run_id)
    assert any(e.event_type == "stage.started"
               for e in rt.store.list_events(run.run_id))


def test_a_failed_attempt_records_the_failure_and_does_not_register_artifacts(
    tmp_path,
):
    rt = runtime(tmp_path, manifest())
    run = rt.submit(task("fail"))
    finished = rt.execute_once(run.run_id)

    assert finished.status is RuntimeStatus.FAILED
    assert finished.terminal_reason == "tool_error"
    view = rt.describe(run.run_id)
    attempt = view["stages"][0]["attempts"][0]
    assert attempt["status"] == "failed"
    # A failed run legitimately produces nothing; it must not be required to.
    assert attempt["artifacts"] == []


def test_a_protocol_violation_is_a_failure_not_a_crash(tmp_path):
    rt = runtime(tmp_path, manifest())
    run = rt.submit(task("lie_success_nonzero_exit"))
    finished = rt.execute_once(run.run_id)
    assert finished.status is RuntimeStatus.FAILED
    assert finished.terminal_reason == "protocol_error"


# --------------------------------------------------------------------------
# forged authority
# --------------------------------------------------------------------------

def test_the_runtime_refuses_an_adapter_that_forges_evaluator_authority(tmp_path):
    rt = runtime(tmp_path, manifest())
    run = rt.submit(task("forge_authority"))
    finished = rt.execute_once(run.run_id)
    assert finished.status is RuntimeStatus.FAILED
    assert finished.terminal_reason == "runtime_error"
    # The forged artifact must not have reached durable state at all.
    attempt = rt.describe(run.run_id)["stages"][0]["attempts"][0]
    assert attempt["artifacts"] == []
    assert "official_qor" not in json.dumps(rt.describe(run.run_id))


def test_an_adapter_may_not_declare_the_runtime_receipt_kind(tmp_path):
    from openroad_platform_contracts import ContractError

    with pytest.raises(ContractError, match="reserved"):
        ArtifactDeclaration(kind="runtime_protocol_receipt", path="x.json").validate()


# --------------------------------------------------------------------------
# the protocol receipt: declarative, not hardcoded per plugin
# --------------------------------------------------------------------------

def test_a_plugin_that_requires_a_receipt_gets_one_without_a_kernel_branch(tmp_path):
    declared_variable = "CAPABILITY_PROTOCOL_RECEIPT"
    rt = runtime(tmp_path, manifest(requirements=RuntimeRequirements(
        require_protocol_receipt=True,
        require_experiment_protocol=True,
        environment_receipt_variable=declared_variable,
    )))
    protocol = {"frozen": True, "budget": 10}
    run = rt.submit(task("ok", inputs={
        "behaviour": "ok", "experiment_protocol": protocol,
    }))
    finished = rt.execute_once(run.run_id)
    assert finished.status is RuntimeStatus.SUCCEEDED

    attempt = rt.describe(run.run_id)["stages"][0]["attempts"][0]
    receipt = next(a for a in attempt["artifacts"]
                   if a["kind"] == "runtime_protocol_receipt")
    on_disk = json.loads(
        (Path(attempt["workspace"]) / receipt["store_key"]).read_text("utf-8")
    )
    assert on_disk["protocol"] == protocol
    assert on_disk["run_id"] == run.run_id


def test_a_receipt_requiring_plugin_without_a_protocol_fails_the_attempt(tmp_path):
    rt = runtime(tmp_path, manifest(requirements=RuntimeRequirements(
        require_protocol_receipt=True,
        require_experiment_protocol=True,
        environment_receipt_variable="CAPABILITY_PROTOCOL_RECEIPT",
    )))
    run = rt.submit(task("ok"))  # no experiment_protocol in inputs
    finished = rt.execute_once(run.run_id)
    assert finished.status is RuntimeStatus.FAILED


def test_a_plugin_without_the_requirement_gets_no_receipt(tmp_path):
    rt = runtime(tmp_path, manifest())
    run = rt.submit(task("ok"))
    rt.execute_once(run.run_id)
    attempt = rt.describe(run.run_id)["stages"][0]["attempts"][0]
    assert all(a["kind"] != "runtime_protocol_receipt"
               for a in attempt["artifacts"])


# --------------------------------------------------------------------------
# the protected evaluator boundary
# --------------------------------------------------------------------------

class RecordingEvaluator:
    def __init__(self, verdict_factory):
        self.requests: list[EvaluationRequest] = []
        self._factory = verdict_factory

    def evaluate(self, request: EvaluationRequest) -> Verdict:
        self.requests.append(request)
        return self._factory(request)


def test_the_protected_evaluator_is_invoked_after_a_successful_adapter(tmp_path):
    def verdict(request: EvaluationRequest) -> Verdict:
        return Verdict(status=VerdictStatus.ADMISSIBLE, metrics=(
            Metric(name="wns_ns", value=-0.05, unit="ns",
                   source_artifact_id="art-from-evaluator"),
        ))

    evaluator = RecordingEvaluator(verdict)
    rt = runtime(tmp_path, manifest(), protected_evaluator=evaluator)
    run = rt.submit(task("ok"))
    rt.execute_once(run.run_id)

    assert len(evaluator.requests) == 1
    assert evaluator.requests[0].task.task_id == "task-ok"


def test_the_evaluator_is_not_invoked_for_a_failed_adapter(tmp_path):
    evaluator = RecordingEvaluator(lambda r: Verdict(
        status=VerdictStatus.INCOMPLETE, reason="should not be called"))
    rt = runtime(tmp_path, manifest(), protected_evaluator=evaluator)
    run = rt.submit(task("fail"))
    rt.execute_once(run.run_id)
    assert evaluator.requests == []


def test_a_rejecting_evaluator_fails_the_run(tmp_path):
    evaluator = RecordingEvaluator(lambda r: Verdict(
        status=VerdictStatus.REJECTED, reason="artifact hash mismatch"))
    rt = runtime(tmp_path, manifest(), protected_evaluator=evaluator)
    run = rt.submit(task("ok"))
    finished = rt.execute_once(run.run_id)
    assert finished.status is RuntimeStatus.FAILED
    assert finished.terminal_reason == "runtime_error"
    # The attempt is terminal too: no run may be left stuck in RUNNING.
    attempt = rt.describe(run.run_id)["stages"][0]["attempts"][0]
    assert attempt["status"] == "failed"


# --------------------------------------------------------------------------
# artifact reads
# --------------------------------------------------------------------------

def test_reading_a_registered_artifact_re_verifies_its_hash(tmp_path):
    rt = runtime(tmp_path, manifest())
    run = rt.submit(task("ok"))
    rt.execute_once(run.run_id)
    attempt = rt.describe(run.run_id)["stages"][0]["attempts"][0]
    report = next(a for a in attempt["artifacts"] if a["kind"] == "report")

    excerpt = rt.read_artifact_excerpt(
        run.run_id, report["artifact_id"], offset=0, max_bytes=1024
    )
    assert "area_um2" in excerpt["text"]

    # Editing the copy in the workspace no longer changes what is served: the
    # artifact was taken into the object store at registration, and the
    # workspace is scratch from that moment on.
    (Path(attempt["workspace"]) / report["store_key"]).write_text(
        "tampered", encoding="utf-8"
    )
    assert "area_um2" in rt.read_artifact_excerpt(
        run.run_id, report["artifact_id"], offset=0, max_bytes=1024
    )["text"]

    # Editing the object behind the runtime's back is still detected, not served.
    obj = rt.store.object_path(report["sha256"])
    assert obj.is_file()
    obj.write_text("tampered", encoding="utf-8")
    with pytest.raises(RuntimeStoreError, match="changed after registration"):
        rt.read_artifact_excerpt(
            run.run_id, report["artifact_id"], offset=0, max_bytes=1024
        )


def test_an_artifact_outlives_the_workspace_that_produced_it(tmp_path):
    """The point of taking custody: an artifact is not a scratch file.

    Until the object store existed the bytes lived in the attempt workspace, so
    a run's evidence was as durable as a directory nobody was managing.
    """
    rt = runtime(tmp_path, manifest())
    run = rt.submit(task("ok"))
    rt.execute_once(run.run_id)
    attempt = rt.describe(run.run_id)["stages"][0]["attempts"][0]
    report = next(a for a in attempt["artifacts"] if a["kind"] == "report")

    shutil.rmtree(attempt["workspace"])
    excerpt = rt.read_artifact_excerpt(
        run.run_id, report["artifact_id"], offset=0, max_bytes=1024
    )
    assert "area_um2" in excerpt["text"]


def test_artifact_excerpt_bounds_are_enforced(tmp_path):
    rt = runtime(tmp_path, manifest())
    run = rt.submit(task("ok"))
    rt.execute_once(run.run_id)
    attempt = rt.describe(run.run_id)["stages"][0]["attempts"][0]
    artifact_id = attempt["artifacts"][0]["artifact_id"]
    with pytest.raises(ValueError, match="bounds are invalid"):
        rt.read_artifact_excerpt(run.run_id, artifact_id, offset=-1, max_bytes=10)
    with pytest.raises(ValueError, match="bounds are invalid"):
        rt.read_artifact_excerpt(run.run_id, artifact_id, offset=0, max_bytes=10**9)


def test_reading_an_unknown_artifact_is_refused(tmp_path):
    rt = runtime(tmp_path, manifest())
    run = rt.submit(task("ok"))
    rt.execute_once(run.run_id)
    with pytest.raises(RuntimeStoreError, match="not registered"):
        rt.read_artifact_excerpt(run.run_id, "art-nope", offset=0, max_bytes=10)


# --------------------------------------------------------------------------
# cancellation
# --------------------------------------------------------------------------

def test_cancellation_is_requested_before_the_attempt_starts(tmp_path):
    rt = runtime(tmp_path, manifest())
    run = rt.submit(task("ok"))
    finished = rt.execute_once(run.run_id, external_cancel_requested=lambda: True)
    # The request is recorded in the runtime's own store, and the run does not
    # silently report success.
    assert finished.status is not RuntimeStatus.SUCCEEDED


def test_a_terminal_run_is_not_executed_again(tmp_path):
    rt = runtime(tmp_path, manifest())
    run = rt.submit(task("ok"))
    rt.execute_once(run.run_id)
    again = rt.execute_once(run.run_id)
    assert again.status is RuntimeStatus.SUCCEEDED
    # Exactly one attempt: a finished run does not silently run twice.
    attempts = rt.store.list_attempts(rt.store.list_stages(run.run_id)[0].stage_run_id)
    assert len(attempts) == 1


# --------------------------------------------------------------------------
# retry: who decides, and who pays
# --------------------------------------------------------------------------

def test_a_retryable_failure_returns_the_run_to_the_queue(tmp_path):
    """The plugin asks for another attempt; the platform grants the budget.

    A retryable failure must NOT settle the run.  If it did, the plugin's
    ``retryable`` flag would be a word nobody acts on -- which is the failure
    mode this round exists to remove.
    """
    rt = runtime(tmp_path, manifest())
    run = rt.submit(task("fail_retryable", max_attempts=2))
    run = rt.execute_once(run.run_id)

    assert run.status is RuntimeStatus.RETRY_WAIT, run.status
    assert not is_terminal(run.status)
    # The attempt that failed stays recorded as a failure: it did fail, and the
    # retry is a new attempt rather than an erasure of the old one.
    stage = rt.store.list_stages(run.run_id)[0]
    attempts = rt.store.list_attempts(stage.stage_run_id)
    assert len(attempts) == 1
    assert attempts[0].status is AttemptStatus.FAILED
    assert attempts[0].failure["category"] == "transient_error"


def test_a_retried_run_can_actually_be_claimed_again(tmp_path):
    """A retry that no worker can pick up is a run that waits forever.

    The claim query requires both the stage and the run to be non-terminal, so
    this asserts the property that makes the retry reachable rather than merely
    recorded.
    """
    rt = runtime(tmp_path, manifest())
    run = rt.submit(task("fail_retryable", max_attempts=2))
    rt.execute_once(run.run_id)
    assert run.run_id in rt.store.runnable_runs()


def test_the_budget_is_respected_and_then_the_run_fails(tmp_path):
    """``max_attempts`` is spent, not ignored in either direction."""
    rt = runtime(tmp_path, manifest())
    run = rt.submit(task("fail_retryable", max_attempts=3))

    run = rt.execute_once(run.run_id)
    assert run.status is RuntimeStatus.RETRY_WAIT
    run = rt.execute_once(run.run_id)
    assert run.status is RuntimeStatus.RETRY_WAIT
    run = rt.execute_once(run.run_id)
    assert run.status is RuntimeStatus.FAILED, run.status

    stage = rt.store.list_stages(run.run_id)[0]
    attempts = rt.store.list_attempts(stage.stage_run_id)
    assert [a.attempt_number for a in attempts] == [1, 2, 3]
    assert all(a.status is AttemptStatus.FAILED for a in attempts)
    # And it is no longer offered to any worker.
    assert run.run_id not in rt.store.runnable_runs()


def test_one_attempt_means_no_retry(tmp_path):
    """The default is no retry: an unasked-for repeat is not a kindness."""
    rt = runtime(tmp_path, manifest())
    run = rt.submit(task("fail_retryable"))          # max_attempts defaults to 1
    run = rt.execute_once(run.run_id)
    assert run.status is RuntimeStatus.FAILED
    stage = rt.store.list_stages(run.run_id)[0]
    assert len(rt.store.list_attempts(stage.stage_run_id)) == 1


def test_a_failure_the_plugin_did_not_mark_retryable_is_not_retried(tmp_path):
    """The platform does not guess.  Silence is not consent to re-run."""
    rt = runtime(tmp_path, manifest())
    run = rt.submit(task("fail", max_attempts=5))
    run = rt.execute_once(run.run_id)
    assert run.status is RuntimeStatus.FAILED
    stage = rt.store.list_stages(run.run_id)[0]
    assert len(rt.store.list_attempts(stage.stage_run_id)) == 1


def test_a_protocol_error_is_never_retried(tmp_path):
    """A plugin bug reproduces on the second run; the budget is not spent on it.

    ``missing_artifact`` is the adapter's declaration not matching what it left
    behind -- deterministic, so retrying would burn an attempt to learn nothing.
    """
    rt = runtime(tmp_path, manifest())
    run = rt.submit(task("missing_artifact", max_attempts=4))
    run = rt.execute_once(run.run_id)
    assert run.status is RuntimeStatus.FAILED
    stage = rt.store.list_stages(run.run_id)[0]
    attempts = rt.store.list_attempts(stage.stage_run_id)
    assert len(attempts) == 1
    assert attempts[0].failure["category"] == "protocol_error"


def test_a_timed_out_run_is_not_retried(tmp_path):
    """A timeout is not a retryable failure: nothing asked for another attempt."""
    # The manifest's own ceiling is what bounds the attempt, so it is set low
    # rather than the task's: the attempt is capped at one second.
    rt = runtime(tmp_path, manifest(default_timeout_seconds=1))
    run = rt.submit(task("noisy", max_attempts=4, timeout_seconds=600))
    run = rt.execute_once(run.run_id)
    assert run.status is RuntimeStatus.TIMED_OUT, run.status
    stage = rt.store.list_stages(run.run_id)[0]
    assert len(rt.store.list_attempts(stage.stage_run_id)) == 1
