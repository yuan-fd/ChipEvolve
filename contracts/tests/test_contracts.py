"""Contract invariants.

These tests exist because a contract that accepts a malformed payload is worse
than no contract: it produces a wrong result with a valid-looking shape.
"""

from __future__ import annotations

import json

import pytest
from openroad_platform_contracts import (
    INPUT_MANIFEST_KIND,
    ArtifactDeclaration,
    AttemptStatus,
    ContractError,
    Event,
    InputFile,
    Metric,
    PluginManifest,
    PluginResult,
    ProgressPhase,
    ProgressReport,
    ProgressStatus,
    RuntimeRequirements,
    RuntimeStatus,
    StagedInput,
    TaskSpec,
    Verdict,
    VerdictStatus,
    attempt_transition_allowed,
    decode_progress_line,
    primitive,
    run_transition_allowed,
)


def make_task(**overrides) -> TaskSpec:
    base = {
        "task_id": "task-1",
        "project_id": "proj-1",
        "design_id": "design-1",
        "plugin_id": "some-capability",
        "inputs": {"benchmark": "gcd"},
    }
    base.update(overrides)
    return TaskSpec(**base)


# --------------------------------------------------------------------------
# task / manifest / result
# --------------------------------------------------------------------------

def test_a_task_must_name_the_plugin_that_runs_it():
    """There is exactly one way to say what runs a task, and it is required.

    ``workflow_id`` used to be the alternative: the contract demanded exactly one
    of the two, no code read either one, and a task that named only a workflow
    failed at the store with "a run stage requires a plugin_id".  A field that is
    validated but neither read nor usable is worse than a missing feature -- it
    tells a caller it can do something it cannot.
    """
    with pytest.raises(ContractError, match="must name the plugin"):
        make_task(plugin_id=None).validate()
    make_task(plugin_id="some-capability").validate()


def test_the_removed_fields_are_refused_rather_than_ignored():
    """A payload from the previous schema version fails loudly.

    Silently dropping ``workflow_id`` would let a caller keep believing their
    orchestration was honoured.
    """
    payload = make_task().to_dict()
    payload["workflow_id"] = "wf-1"
    with pytest.raises(ContractError, match="unknown TaskSpec fields: workflow_id"):
        TaskSpec.from_dict(payload)



def test_the_resource_field_came_back_with_a_different_shape():
    """``resources`` was deleted for being a promise; it returns with behaviour.

    The old shape was declared and enforced nowhere, which is why it was
    removed.  What came back is a different contract: aggregate bounds on the
    attempt's process tree, measured by the platform.  A payload written against
    the old shape is refused rather than half-read, because reading a field
    whose meaning changed is how a caller keeps believing something is honoured.
    """
    payload = make_task().to_dict()
    payload["resources"] = {"cpu": 4}
    with pytest.raises(ContractError, match="unknown ResourceRequest fields: cpu"):
        TaskSpec.from_dict(payload)


def test_task_rejects_unknown_field_on_load():
    payload = make_task().to_dict()
    payload["surprise"] = 1
    with pytest.raises(ContractError, match="unknown"):
        TaskSpec.from_dict(payload)


def test_task_rejects_wrong_schema_version():
    payload = make_task().to_dict()
    payload["schema_version"] = 99
    with pytest.raises(ContractError, match="schema_version"):
        TaskSpec.from_dict(payload)


def test_task_rejects_path_traversal_in_ids():
    with pytest.raises(ContractError):
        make_task(task_id="../../etc/passwd").validate()


def test_task_round_trips():
    task = make_task(labels={"teaching_mode": "guided"})
    assert TaskSpec.from_dict(task.to_dict()) == task


def test_task_can_pin_an_external_toolkit_version():
    task = make_task(plugin_version="2.1.0")

    restored = TaskSpec.from_dict(task.to_dict())

    assert restored.plugin_version == "2.1.0"


def test_task_rejects_an_invalid_toolkit_version():
    with pytest.raises(ContractError, match="plugin_version"):
        make_task(plugin_version="../latest").validate()


def test_manifest_rejects_reserved_artifact_kind():
    manifest = PluginManifest(
        plugin_id="x", plugin_version="1",
        adapter_entry=("python3", "./adapter.py"),
        capabilities=("do.thing",), supported_arch=("aarch64",),
        artifact_rules=({"kind": "protected_evaluation"},),
    )
    manifest.validate()  # manifest rules do not inspect kinds
    ArtifactDeclaration(kind="fine", path="out.log").validate()
    with pytest.raises(ContractError, match="reserved"):
        ArtifactDeclaration(kind="protected_evaluation", path="x").validate()


def test_manifest_requirements_are_declarative_not_hardcoded():
    """The kernel learns a capability's needs from data, never from its name."""
    manifest = PluginManifest(
        plugin_id="anything", plugin_version="1",
        adapter_entry=("python3", "./a.py"),
        capabilities=("x",), supported_arch=("aarch64",),
        requirements=RuntimeRequirements(
            require_protocol_receipt=True,
            require_experiment_protocol=True,
            environment_receipt_variable="CAPABILITY_PROTOCOL_RECEIPT",
        ),
    )
    manifest.validate()
    restored = PluginManifest.from_dict(manifest.to_dict())
    assert restored.requirements.require_protocol_receipt is True
    assert restored.requirements.environment_receipt_variable == \
        "CAPABILITY_PROTOCOL_RECEIPT"


def test_receipt_requirement_needs_a_variable_name():
    with pytest.raises(ContractError, match="environment_receipt_variable"):
        RuntimeRequirements(require_protocol_receipt=True).validate()


def test_result_must_not_claim_success_with_a_failing_exit_code():
    with pytest.raises(ContractError, match="exit_code 0"):
        PluginResult(
            status=RuntimeStatus.SUCCEEDED, exit_code=1,
            started_at="t0", ended_at="t1",
        ).validate()


def test_result_rejects_non_terminal_status():
    with pytest.raises(ContractError, match="terminal"):
        PluginResult(
            status=RuntimeStatus.RUNNING, exit_code=0,
            started_at="t0", ended_at="t1",
        ).validate()


# --------------------------------------------------------------------------
# state machines
# --------------------------------------------------------------------------

def test_terminal_run_status_accepts_no_transition():
    for status in (RuntimeStatus.SUCCEEDED, RuntimeStatus.FAILED,
                   RuntimeStatus.CANCELLED, RuntimeStatus.TIMED_OUT,
                   RuntimeStatus.LOST):
        assert not run_transition_allowed(status, RuntimeStatus.RUNNING)


def test_a_lost_attempt_is_terminal():
    assert not attempt_transition_allowed(AttemptStatus.LOST, AttemptStatus.RUNNING)
    assert attempt_transition_allowed(AttemptStatus.PENDING, AttemptStatus.RUNNING)


def test_event_round_trips_and_rejects_bad_identifiers():
    event = Event(
        event_id="e1", run_id="r1", event_type="stage.started",
        occurred_at="2026-01-01T00:00:00Z", producer="runtime",
        payload={"stage": "synthesis"},
    )
    assert Event.from_dict(event.to_dict()) == event
    with pytest.raises(ContractError):
        Event(
            event_id="bad id", run_id="r1", event_type="t",
            occurred_at="now", producer="runtime",
        ).validate()


# --------------------------------------------------------------------------
# the progress envelope -- the thing that keeps the kernel generic
# --------------------------------------------------------------------------

def test_progress_envelope_decodes_without_knowing_stage_names():
    line = '[progress] {"stage": "any-vendor-stage", "phase": "started"}'
    report = decode_progress_line(line)
    assert report is not None
    assert report.stage == "any-vendor-stage"
    assert report.phase is ProgressPhase.STARTED


def test_ordinary_log_output_is_not_a_progress_envelope():
    assert decode_progress_line("Running synthesis...") is None
    assert decode_progress_line("") is None


def test_progress_marker_is_configurable_per_plugin():
    line = '<<stage>> {"stage": "x", "phase": "started"}'
    assert decode_progress_line(line, marker="[progress]") is None
    report = decode_progress_line(line, marker="<<stage>>")
    assert report is not None and report.stage == "x"


def test_progress_rejects_malformed_or_hostile_payloads():
    with pytest.raises(ContractError, match="JSON"):
        decode_progress_line('[progress] {not json}')
    with pytest.raises(ContractError, match="status"):
        decode_progress_line('[progress] {"stage": "x", "phase": "finished"}')
    with pytest.raises(ContractError, match="control characters"):
        ProgressReport(stage="a\nb", phase=ProgressPhase.STARTED).validate()
    with pytest.raises(ContractError, match="64"):
        ProgressReport(stage="x" * 65, phase=ProgressPhase.STARTED).validate()


def test_progress_started_must_not_carry_a_status():
    with pytest.raises(ContractError, match="must not carry"):
        ProgressReport(
            stage="x", phase=ProgressPhase.STARTED,
            status=ProgressStatus.SUCCEEDED,
        ).validate()


# --------------------------------------------------------------------------
# evidence rules
# --------------------------------------------------------------------------

def test_metric_requires_a_source_artifact_to_be_evidence():
    metric = Metric(name="wns", value=1.0)
    metric.validate()  # a bare metric is allowed as a value...
    with pytest.raises(ContractError, match="not evidence"):
        Verdict(status=VerdictStatus.ADMISSIBLE, metrics=(metric,)).validate()


def test_admissible_verdict_needs_metrics_and_rejected_needs_a_reason():
    with pytest.raises(ContractError, match="at least one metric"):
        Verdict(status=VerdictStatus.ADMISSIBLE).validate()
    with pytest.raises(ContractError, match="reason"):
        Verdict(status=VerdictStatus.REJECTED).validate()
    Verdict(status=VerdictStatus.INCOMPLETE, reason="missing stage report").validate()


def test_metric_may_not_forge_kernel_authority():
    with pytest.raises(ContractError, match="kernel-reserved"):
        Metric(name="wns", value=1.0, context={"official_qor": True}).validate()


def test_artifact_path_must_stay_inside_the_workspace():
    with pytest.raises(ContractError, match="inside the attempt workspace"):
        ArtifactDeclaration(kind="log", path="../../etc/shadow").validate()
    with pytest.raises(ContractError, match="inside the attempt workspace"):
        ArtifactDeclaration(kind="log", path="/etc/shadow").validate()
    ArtifactDeclaration(kind="log", path="reports/area.rpt").validate()


def test_adapter_may_not_declare_runtime_authority():
    with pytest.raises(ContractError, match="runtime_authority"):
        ArtifactDeclaration(
            kind="log", path="a.log",
            metadata={"runtime_authority": "protected_evaluator"},
        ).validate()


# --------------------------------------------------------------------------
# serialisation
# --------------------------------------------------------------------------

def test_primitive_output_is_json_serialisable():
    payload = primitive(make_task(labels={"a": "b"}))
    json.dumps(payload)
    assert payload["schema_version"] == 3
    assert payload["labels"] == {"a": "b"}


# --------------------------------------------------------------------------
# staged inputs
# --------------------------------------------------------------------------

def test_a_task_may_declare_the_files_the_platform_must_place():
    spec = make_task(staged_inputs=(
        InputFile(source="/data/netlist.v", destination="design/netlist.v"),
    ))
    spec.validate()
    reloaded = TaskSpec.from_dict(spec.to_dict())
    assert reloaded == spec
    assert reloaded.staged_inputs[0].required is True


def test_a_task_may_reference_an_uploaded_input():
    spec = make_task(staged_inputs=(
        InputFile(input_id="input-123", destination="inputs/place.tcl"),
    ))

    assert TaskSpec.from_dict(spec.to_dict()) == spec


def test_an_input_may_not_name_two_platform_sources():
    with pytest.raises(ContractError, match="exactly one"):
        InputFile(
            input_id="input-123", artifact_id="art-123", destination="x"
        ).validate()


def test_a_payload_without_staged_inputs_still_loads():
    """Adding an optional field must not invalidate what was already written.

    Every plugin shipped before this change emits a task payload with no
    ``staged_inputs`` key, and the two external capabilities this platform is
    built to host are exactly those.  A version bump here would have broken
    them, and the field is genuinely optional, so there is none.
    """
    payload = make_task().to_dict()
    del payload["staged_inputs"]
    assert TaskSpec.from_dict(payload).staged_inputs == ()


def test_an_input_source_must_be_absolute():
    """A relative source resolves against whichever worker picked the run up.

    Two workers with different working directories would then stage different
    bytes for the same task while every record insisted it was the same task.
    """
    with pytest.raises(ContractError, match="absolute"):
        make_task(staged_inputs=(
            InputFile(source="design.v", destination="design.v"),
        )).validate()


def test_an_input_destination_may_not_leave_the_workspace():
    for destination in ("../outside.v", "/etc/passwd"):
        with pytest.raises(ContractError, match="attempt workspace"):
            InputFile(source="/data/a.v", destination=destination).validate()


def test_two_inputs_may_not_claim_the_same_destination():
    with pytest.raises(ContractError, match="same destination"):
        make_task(staged_inputs=(
            InputFile(source="/data/a.v", destination="same.v"),
            InputFile(source="/data/b.v", destination="same.v"),
        )).validate()


def test_the_manifest_kind_is_reserved_from_adapters():
    """The input manifest is the platform's record of what it placed.

    A plugin that could register it could make the record disagree with the
    bytes, which is the one thing the record exists to prevent.
    """
    with pytest.raises(ContractError, match="reserved"):
        ArtifactDeclaration(
            kind=INPUT_MANIFEST_KIND, path="input_manifest.json",
        ).validate()


def test_a_staged_input_records_a_digest_only_when_it_is_present():
    present = StagedInput(
        destination="a.v", source="/data/a.v", present=True,
        size_bytes=3, sha256="a" * 64,
    )
    present.validate()

    absent = StagedInput(
        destination="a.v", source="/data/a.v", present=False, size_bytes=0,
    )
    absent.validate()
    assert absent.sha256 is None

    with pytest.raises(ContractError, match="needs a sha256"):
        StagedInput(
            destination="a.v", source="/data/a.v", present=True, size_bytes=3,
        ).validate()
    with pytest.raises(ContractError, match="may not carry a sha256"):
        StagedInput(
            destination="a.v", source="/data/a.v", present=False, size_bytes=0,
            sha256="a" * 64,
        ).validate()
