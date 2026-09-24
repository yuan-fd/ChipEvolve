"""Staged inputs: the platform places the bytes, and measures what it placed.

The property under test is the one ``design_id`` could never provide.  A label
the caller chooses cannot make two runs comparable; a digest the platform
measured can.  Every test here is a way that claim could be false.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from openroad_platform_contracts import (
    INPUT_MANIFEST_FILENAME,
    INPUT_MANIFEST_KIND,
    InputFile,
    PluginManifest,
    RuntimeStatus,
    TaskSpec,
)
from openroad_platform_runtime import (
    InputStagingError,
    RuntimeStore,
    RuntimeStoreError,
    WorkflowRuntime,
)
from openroad_platform_runtime.adapter import ProcessAdapter
from openroad_platform_runtime.guardian import ProcessGuardian

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_adapter.py"


class Resolver:
    def __init__(self, manifest: PluginManifest):
        self.manifest = manifest

    def resolve(self, plugin_id, *, version=None, capability=None, arch=None):
        if plugin_id != self.manifest.plugin_id:
            raise LookupError(plugin_id)
        return self.manifest


def manifest(**overrides) -> PluginManifest:
    base = {
        "plugin_id": "fake-capability",
        "plugin_version": "1.0.0",
        "adapter_entry": (sys.executable, str(FIXTURE)),
        "capabilities": ("do.thing",),
        "supported_arch": ("aarch64", "x86_64", "arm64"),
        "artifact_rules": (
            {"kind": "report", "required": True},
            {"kind": "log", "required": False},
        ),
        "default_timeout_seconds": 60,
    }
    base.update(overrides)
    return PluginManifest(**base)


def runtime(tmp_path: Path) -> WorkflowRuntime:
    return WorkflowRuntime(
        RuntimeStore(tmp_path / "runtime.db"), Resolver(manifest()),
        workspace_root=tmp_path / "ws",
        adapter=ProcessAdapter(
            ProcessGuardian(poll_interval=0.02, terminate_grace=1.0)
        ),
        lease_seconds=30, worker_id="test-worker",
    )


def task(name: str, *inputs: InputFile, behaviour: str = "ok") -> TaskSpec:
    return TaskSpec(
        task_id=f"task-{name}", project_id="p", design_id="d",
        plugin_id="fake-capability",
        inputs={"behaviour": behaviour},
        staged_inputs=tuple(inputs),
        timeout_seconds=30,
    )


def write_source(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / "sources" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def only_attempt(rt: WorkflowRuntime, run_id: str):
    return rt.store.list_attempts(rt.store.list_stages(run_id)[0].stage_run_id)[0]


def run_to_completion(rt: WorkflowRuntime, spec: TaskSpec):
    run = rt.submit(spec)
    return rt.execute_once(run.run_id)


def manifest_digest(rt: WorkflowRuntime, attempt_id: str) -> str:
    for artifact in rt.store.list_artifacts(attempt_id):
        if artifact.kind == INPUT_MANIFEST_KIND:
            return artifact.sha256
    raise AssertionError("no input manifest was registered")


# --------------------------------------------------------------------------
# the platform places the bytes
# --------------------------------------------------------------------------

def test_a_declared_input_lands_where_the_task_said_it_would(tmp_path):
    source = write_source(tmp_path, "design.v", "module top; endmodule\n")
    rt = runtime(tmp_path)
    run = run_to_completion(rt, task(
        "ok", InputFile(source=str(source), destination="design/design.v"),
    ))

    attempt = only_attempt(rt, run.run_id)
    landed = Path(attempt.workspace) / "design/design.v"
    assert landed.read_text(encoding="utf-8") == "module top; endmodule\n"

    recorded = rt.store.list_inputs(attempt.attempt_id)
    assert len(recorded) == 1
    assert recorded[0].destination == "design/design.v"
    assert recorded[0].present is True
    assert recorded[0].size_bytes == landed.stat().st_size
    assert recorded[0].sha256 is not None


def test_workspace_creation_failure_releases_the_attempt(tmp_path):
    rt = runtime(tmp_path)
    run = rt.submit(task("blocked-workspace"))
    stage = rt.store.list_stages(run.run_id)[0]
    blocked = tmp_path / "ws" / run.run_id / stage.stage_run_id / "attempt-1"
    blocked.parent.mkdir(parents=True)
    blocked.write_text("a file blocks the workspace", encoding="utf-8")

    result = rt.execute_once(run.run_id)

    assert result.status is RuntimeStatus.FAILED
    attempt = only_attempt(rt, run.run_id)
    assert attempt.status.value == "failed"
    with rt.store._connection:
        reservations = rt.store._connection.execute(
            "SELECT * FROM runtime_resource_reservations WHERE attempt_id = ?",
            (attempt.attempt_id,),
        ).fetchall()
    assert reservations == []


def test_the_plugin_and_the_platform_measure_the_same_bytes(tmp_path):
    """The strongest form of the claim: two independent measurements agree.

    The platform digests what it copied; the adapter digests what it read.  If
    staging placed anything other than the declared bytes -- a truncation, a
    stale file, the wrong source -- the two digests differ and this fails.
    """
    source = write_source(tmp_path, "design.v", "module top; endmodule\n")
    rt = runtime(tmp_path)
    run = run_to_completion(rt, task(
        "echo", InputFile(source=str(source), destination="design.v"),
        behaviour="echo_input",
    ))

    attempt = only_attempt(rt, run.run_id)
    recorded = rt.store.list_inputs(attempt.attempt_id)[0]
    seen = json.loads(
        (Path(attempt.workspace) / "report.json").read_text(encoding="utf-8")
    )
    assert seen["input_sha256"] == recorded.sha256
    assert seen["bytes"] == recorded.size_bytes


def test_the_input_manifest_is_registered_as_platform_owned_evidence(tmp_path):
    source = write_source(tmp_path, "design.v", "x")
    rt = runtime(tmp_path)
    run = run_to_completion(rt, task(
        "manifest", InputFile(source=str(source), destination="design.v"),
    ))
    attempt = only_attempt(rt, run.run_id)

    kinds = {a.kind for a in rt.store.list_artifacts(attempt.attempt_id)}
    assert INPUT_MANIFEST_KIND in kinds
    assert (Path(attempt.workspace) / INPUT_MANIFEST_FILENAME).is_file()


def test_a_task_without_staged_inputs_writes_no_manifest(tmp_path):
    rt = runtime(tmp_path)
    run = run_to_completion(rt, task("plain"))
    attempt = only_attempt(rt, run.run_id)
    assert rt.store.list_inputs(attempt.attempt_id) == []
    assert not (Path(attempt.workspace) / INPUT_MANIFEST_FILENAME).exists()
    assert INPUT_MANIFEST_KIND not in {
        a.kind for a in rt.store.list_artifacts(attempt.attempt_id)
    }


# --------------------------------------------------------------------------
# what the read model exposes
# --------------------------------------------------------------------------

def test_describe_run_projects_the_inputs_of_each_attempt(tmp_path):
    """An app may not open the kernel database (G5).

    A fact the read model omits is a fact no application can ever show, so the
    projection is the feature, not a convenience.
    """
    source = write_source(tmp_path, "design.v", "abc")
    rt = runtime(tmp_path)
    run = run_to_completion(rt, task(
        "project", InputFile(source=str(source), destination="in/design.v"),
    ))

    view = rt.describe(run.run_id)
    inputs = view["stages"][0]["attempts"][0]["inputs"]
    assert [i["destination"] for i in inputs] == ["in/design.v"]
    assert inputs[0]["sha256"] is not None
    assert inputs[0]["present"] is True


# --------------------------------------------------------------------------
# refusals
# --------------------------------------------------------------------------

def test_a_missing_required_input_is_refused_before_a_run_exists(tmp_path):
    rt = runtime(tmp_path)
    missing = InputFile(source=str(tmp_path / "nope.v"), destination="nope.v")
    with pytest.raises(InputStagingError, match="not a readable file"):
        rt.submit(task("missing", missing))
    assert rt.store.find_run_by_task_id("task-missing") is None


def test_an_absent_optional_input_is_recorded_as_absent(tmp_path):
    rt = runtime(tmp_path)
    optional = InputFile(
        source=str(tmp_path / "maybe.sdc"), destination="maybe.sdc",
        required=False,
    )
    run = run_to_completion(rt, task("optional", optional))
    attempt = only_attempt(rt, run.run_id)

    recorded = rt.store.list_inputs(attempt.attempt_id)
    assert recorded[0].present is False
    # No digest, deliberately: digesting the empty string would make two
    # different absences look like the same empty file.
    assert recorded[0].sha256 is None
    assert not (Path(attempt.workspace) / "maybe.sdc").exists()


def test_a_frozen_input_survives_source_deletion_after_submit(tmp_path):
    source = write_source(tmp_path, "design.v", "hello")
    rt = runtime(tmp_path)
    spec = task("vanish", InputFile(source=str(source), destination="design.v"))
    run = rt.submit(spec)

    source.unlink()
    finished = rt.execute_once(run.run_id)

    assert finished.status is RuntimeStatus.SUCCEEDED
    attempt = only_attempt(rt, run.run_id)
    recorded = rt.store.list_inputs(attempt.attempt_id)[0]
    assert recorded.source_input_id is not None
    assert recorded.sha256


def test_an_adapter_that_rewrites_the_input_manifest_fails(tmp_path):
    """The manifest is the platform's record of what it placed.

    An adapter that could edit it could make the record disagree with the bytes,
    which is the one thing the record exists to prevent.
    """
    source = write_source(tmp_path, "design.v", "hello")
    rt = runtime(tmp_path)
    run = run_to_completion(rt, task(
        "tamper", InputFile(source=str(source), destination="design.v"),
        behaviour="tamper_with_input_manifest",
    ))
    assert run.status is RuntimeStatus.FAILED
    attempt = only_attempt(rt, run.run_id)
    assert INPUT_MANIFEST_KIND in attempt.failure["message"]


def test_two_inputs_at_the_same_destination_are_refused(tmp_path):
    from openroad_platform_contracts import ContractError

    with pytest.raises(ContractError, match="same destination"):
        task(
            "collide",
            InputFile(source="/a/x.v", destination="same.v"),
            InputFile(source="/b/y.v", destination="same.v"),
        ).validate()


# --------------------------------------------------------------------------
# the property: two runs are comparable, and a difference is visible
# --------------------------------------------------------------------------

def test_two_runs_over_the_same_bytes_share_one_identity(tmp_path):
    """Different labels, different source paths, same bytes, same digest.

    The source path is deliberately *not* in the manifest.  An identity document
    that recorded where each caller kept its files would differ between these
    two runs, and the digest would then be measuring the caller's filing habits
    rather than the design.
    """
    first_source = write_source(tmp_path, "copy-a.v", "module top; endmodule\n")
    second_source = write_source(tmp_path, "copy-b.v", "module top; endmodule\n")

    rt = runtime(tmp_path)
    first = run_to_completion(rt, task(
        "same-a", InputFile(source=str(first_source), destination="design.v"),
    ))
    second = run_to_completion(rt, task(
        "same-b", InputFile(source=str(second_source), destination="design.v"),
    ))

    a = manifest_digest(rt, only_attempt(rt, first.run_id).attempt_id)
    b = manifest_digest(rt, only_attempt(rt, second.run_id).attempt_id)
    assert a == b

    # And the source is still recorded, in the platform's own table, where it
    # is provenance rather than identity.
    first_attempt = only_attempt(rt, first.run_id)
    assert rt.store.list_inputs(first_attempt.attempt_id)[0].source == str(
        first_source
    )


def test_two_runs_over_different_bytes_are_distinguishable(tmp_path):
    same = write_source(tmp_path, "one.v", "module top; endmodule\n")
    other = write_source(tmp_path, "two.v", "module other; endmodule\n")

    rt = runtime(tmp_path)
    first = run_to_completion(rt, task(
        "diff-a", InputFile(source=str(same), destination="design.v"),
    ))
    second = run_to_completion(rt, task(
        "diff-b", InputFile(source=str(other), destination="design.v"),
    ))

    a = manifest_digest(rt, only_attempt(rt, first.run_id).attempt_id)
    b = manifest_digest(rt, only_attempt(rt, second.run_id).attempt_id)
    assert a != b


def test_resubmitting_under_the_same_task_id_with_changed_inputs_is_refused(
    tmp_path,
):
    """``task_id`` is not a promise that the design did not move.

    Without this, a caller could resubmit the same id against an edited source
    and be handed the earlier run, which recorded the earlier bytes.
    """
    source = write_source(tmp_path, "design.v", "first")
    rt = runtime(tmp_path)
    rt.submit_idempotent(task(
        "idem", InputFile(source=str(source), destination="design.v"),
    ))

    edited = write_source(tmp_path, "design.v.edited", "second")
    with pytest.raises(RuntimeStoreError, match="different"):
        rt.submit_idempotent(task(
            "idem", InputFile(source=str(edited), destination="design.v"),
        ))

    # And the same declaration is still idempotent.
    assert rt.submit_idempotent(task(
        "idem", InputFile(source=str(source), destination="design.v"),
    )).run_id is not None


# --------------------------------------------------------------------------
# an input may come from an artifact the platform already holds
# --------------------------------------------------------------------------

def produce_a_report(rt: WorkflowRuntime, name: str):
    """Run the fake capability and hand back the artifact it produced."""
    run = run_to_completion(rt, task(name))
    attempt = only_attempt(rt, run.run_id)
    report = next(
        a for a in rt.store.list_artifacts(attempt.attempt_id)
        if a.kind == "report"
    )
    return run, attempt, report


def test_an_input_can_be_an_artifact_another_run_produced(tmp_path):
    """The point of the pair: one run consumes what another produced, with no
    filesystem path passing between them and no re-measuring from a host."""
    rt = runtime(tmp_path)
    _, _, report = produce_a_report(rt, "producer")

    run = run_to_completion(rt, task(
        "consumer",
        InputFile(destination="previous/report.json",
                  artifact_id=report.artifact_id),
    ))
    attempt = only_attempt(rt, run.run_id)

    landed = Path(attempt.workspace) / "previous/report.json"
    assert landed.is_file()

    recorded = rt.store.list_inputs(attempt.attempt_id)[0]
    assert recorded.source_artifact_id == report.artifact_id
    assert recorded.source is None
    assert recorded.sha256 == report.sha256
    assert recorded.size_bytes == report.size_bytes


def test_a_remote_uploaded_input_is_staged_and_recorded(tmp_path):
    rt = runtime(tmp_path)
    uploaded = rt.store.ingest_input(b"set place_density 0.72\n")

    run = run_to_completion(rt, task(
        "uploaded", InputFile(
            destination="inputs/place.tcl", input_id=uploaded.input_id,
        ),
    ))

    attempt = only_attempt(rt, run.run_id)
    assert (Path(attempt.workspace) / "inputs/place.tcl").read_bytes() == (
        b"set place_density 0.72\n"
    )
    recorded = rt.store.list_inputs(attempt.attempt_id)[0]
    assert recorded.source_input_id == uploaded.input_id
    assert recorded.source is None
    assert recorded.source_artifact_id is None
    assert recorded.sha256 == uploaded.sha256


def test_a_referenced_artifact_survives_the_workspace_that_made_it(tmp_path):
    """Two runs chained through the object store, after the first is gone.

    If a reference were resolved through the producer's workspace this is where
    it would break, and it would break for every run whose scratch directory had
    been cleaned up -- which is all of them, eventually.
    """
    rt = runtime(tmp_path)
    _, producer_attempt, report = produce_a_report(rt, "chained-producer")
    shutil.rmtree(producer_attempt.workspace)

    run = run_to_completion(rt, task(
        "chained-consumer",
        InputFile(destination="in/report.json", artifact_id=report.artifact_id),
    ))
    attempt = only_attempt(rt, run.run_id)
    assert run.status is RuntimeStatus.SUCCEEDED
    assert (Path(attempt.workspace) / "in/report.json").is_file()


def test_an_unknown_artifact_id_is_refused_at_submission(tmp_path):
    rt = runtime(tmp_path)
    with pytest.raises(InputStagingError, match="does not have"):
        rt.submit(task(
            "dangling",
            InputFile(destination="in.json", artifact_id="art-does-not-exist"),
        ))
    assert rt.store.find_run_by_task_id("task-dangling") is None


def test_a_reference_whose_bytes_have_gone_fails_the_attempt(tmp_path):
    """The digest is checked as the bytes are copied, so a corrupt store is a
    failure rather than a run that silently reads the wrong design."""
    rt = runtime(tmp_path)
    _, _, report = produce_a_report(rt, "corrupt-producer")
    rt.store.object_path(report.sha256).write_bytes(b"not the report any more")

    spec = task(
        "corrupt-consumer",
        InputFile(destination="in/report.json", artifact_id=report.artifact_id),
    )
    run = rt.submit(spec)
    finished = rt.execute_once(run.run_id)

    assert finished.status is RuntimeStatus.FAILED
    failure = only_attempt(rt, run.run_id).failure
    assert "does not match its own record" in failure["message"]


def test_an_absent_optional_artifact_reference_is_not_an_error(tmp_path):
    """A reference to bytes that are gone is optional if the task says so."""
    rt = runtime(tmp_path)
    _, _, report = produce_a_report(rt, "optional-producer")
    rt.store.object_path(report.sha256).unlink()

    run = run_to_completion(rt, task(
        "optional-consumer",
        InputFile(destination="in/report.json", artifact_id=report.artifact_id,
                  required=False),
    ))
    assert run.status is RuntimeStatus.SUCCEEDED
    recorded = rt.store.list_inputs(only_attempt(rt, run.run_id).attempt_id)[0]
    assert recorded.present is False
    assert recorded.source_artifact_id == report.artifact_id
    assert recorded.sha256 is None
