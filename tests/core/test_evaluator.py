"""The protected evaluation boundary.

An evaluator's word is what makes a QoR number storable, so these tests are
mostly about refusing verdicts.  An evaluator that can say anything and be
believed is worse than no evaluator: it launders an assertion into evidence.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from openroad_platform_contracts import (
    ContractError,
    EvaluationRequest,
    Metric,
    PluginManifest,
    PROTECTED_EVALUATOR_CAPABILITY,
    TaskSpec,
    Verdict,
    VerdictStatus,
)
from openroad_platform_evaluator import (
    EvaluationError,
    PluginBackedEvaluator,
    resolve_evaluator,
    VERDICT_ARTIFACT_KIND,
)
from openroad_platform_runtime import ProcessAdapter
from openroad_platform_runtime.guardian import ProcessGuardian

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_evaluator_adapter.py"


def evaluator_manifest(**overrides) -> PluginManifest:
    base = dict(
        plugin_id="fake-evaluator",
        plugin_version="1.0.0",
        adapter_entry=(sys.executable, str(FIXTURE)),
        capabilities=(PROTECTED_EVALUATOR_CAPABILITY,),
        supported_arch=("aarch64", "x86_64", "arm64"),
        artifact_rules=({"kind": VERDICT_ARTIFACT_KIND, "required": True},),
        default_timeout_seconds=60,
    )
    base.update(overrides)
    return PluginManifest(**base)


def evaluated_task(behaviour: str) -> TaskSpec:
    return TaskSpec(
        task_id="evaluated", project_id="p", design_id="d",
        plugin_id="some-capability", inputs={"behaviour": behaviour},
    )


def run_evaluation(tmp_path: Path, behaviour: str, **manifest_overrides):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "report.json").write_text(
        json.dumps({"area_um2": 42.0}), encoding="utf-8"
    )
    evaluator = PluginBackedEvaluator(
        adapter=ProcessAdapter(
            ProcessGuardian(poll_interval=0.02, terminate_grace=1.0)
        ),
        manifest=evaluator_manifest(**manifest_overrides),
    )
    request = EvaluationRequest(
        manifest=PluginManifest(
            plugin_id="some-capability", plugin_version="1.0.0",
            adapter_entry=("python3", "./a.py"), capabilities=("do.thing",),
            supported_arch=("aarch64",), ),
        task=evaluated_task(behaviour),
        workspace=str(workspace),
        attempt_id="attempt-1",
    )
    return evaluator, request


# --------------------------------------------------------------------------
# construction
# --------------------------------------------------------------------------

def test_a_plugin_without_the_capability_cannot_be_the_evaluator():
    with pytest.raises(EvaluationError, match="does not declare"):
        PluginBackedEvaluator(
            adapter=ProcessAdapter(), manifest=evaluator_manifest(
                capabilities=("do.thing",)
            ),
        )


def test_the_evaluator_identity_is_pinned_by_manifest_hash(tmp_path):
    evaluator, _ = run_evaluation(tmp_path, "ok")
    pin = evaluator.pin
    assert pin.plugin_id == "fake-evaluator"
    assert len(pin.manifest_sha256) == 64
    # A different manifest is a different pin: two verdicts from different
    # evaluator versions are not comparable.
    other, _ = run_evaluation(tmp_path, "ok", plugin_version="2.0.0")
    assert other.pin.manifest_sha256 != pin.manifest_sha256


def test_resolve_evaluator_finds_exactly_one_admitted_candidate():
    class Row:
        def __init__(self, manifest, executable):
            self.manifest = manifest
            self.executable = executable

    class Registry:
        def __init__(self, rows):
            self._rows = rows

        def list(self):
            return self._rows

    admitted = Row(evaluator_manifest(), True)
    blocked = Row(evaluator_manifest(plugin_id="other"), False)
    not_an_evaluator = Row(
        evaluator_manifest(plugin_id="plain", capabilities=("do.thing",)), True
    )
    found = resolve_evaluator(Registry([admitted, blocked, not_an_evaluator]))
    assert found.plugin_id == "fake-evaluator"

    # A blocked evaluator is not a candidate, even though it declares the
    # capability.
    with pytest.raises(EvaluationError, match="no admitted plugin"):
        resolve_evaluator(Registry([blocked]))

    # Two admitted evaluators would make QoR depend on which one was picked.
    with pytest.raises(EvaluationError, match="several plugins"):
        resolve_evaluator(Registry([admitted, Row(evaluator_manifest(
            plugin_id="second"), True)]))


# --------------------------------------------------------------------------
# honest verdicts
# --------------------------------------------------------------------------

def test_an_honest_verdict_is_returned_with_its_evidence(tmp_path):
    evaluator, request = run_evaluation(tmp_path, "ok")
    outcome = evaluator.evaluate_with_evidence(request)

    assert outcome.verdict.status is VerdictStatus.ADMISSIBLE
    assert outcome.verdict.metrics[0].name == "area_um2"
    assert outcome.verdict.metrics[0].value == 42.0
    assert len(outcome.verdict_sha256) == 64
    assert Path(outcome.verdict_path).is_file()


def test_the_evaluator_is_held_to_the_same_artifact_rules_as_an_adapter(tmp_path):
    """It produces a reserved kind, which only the platform may register."""
    evaluator, request = run_evaluation(tmp_path, "ok")
    outcome = evaluator.evaluate_with_evidence(request)
    assert Path(outcome.verdict_path).name == "protected_evaluation.json"


# --------------------------------------------------------------------------
# verdicts that must be refused
# --------------------------------------------------------------------------

def test_an_incomplete_verdict_must_state_a_reason(tmp_path):
    evaluator, request = run_evaluation(tmp_path, "abstain")
    outcome = evaluator.evaluate_with_evidence(request)
    # Abstaining is allowed and honest: it is how "we could not measure this"
    # avoids being reported as a number.
    assert outcome.verdict.status is VerdictStatus.INCOMPLETE
    assert outcome.verdict.reason


def test_a_rejection_is_surfaced_rather_than_stored_as_a_result(tmp_path):
    evaluator, request = run_evaluation(tmp_path, "reject")
    outcome = evaluator.evaluate_with_evidence(request)
    assert outcome.verdict.status is VerdictStatus.REJECTED
    assert "does not match" in outcome.verdict.reason


def test_a_metric_with_no_source_is_refused(tmp_path):
    evaluator, request = run_evaluation(tmp_path, "unsourced")
    with pytest.raises(EvaluationError, match="cites no source"):
        evaluator.evaluate_with_evidence(request)


def test_a_metric_citing_a_nonexistent_artifact_is_refused(tmp_path):
    evaluator, request = run_evaluation(tmp_path, "phantom_metric")
    with pytest.raises(EvaluationError, match="does not exist"):
        evaluator.evaluate_with_evidence(request)


def test_a_metric_citing_a_path_outside_the_workspace_is_refused(tmp_path):
    evaluator, request = run_evaluation(tmp_path, "escape")
    with pytest.raises(EvaluationError, match="outside the workspace"):
        evaluator.evaluate_with_evidence(request)


def test_a_missing_workspace_is_refused_before_anything_runs(tmp_path):
    evaluator, request = run_evaluation(tmp_path, "ok")
    broken = EvaluationRequest(
        manifest=request.manifest, task=request.task,
        workspace=str(tmp_path / "absent"), attempt_id="attempt-1",
    )
    with pytest.raises(EvaluationError, match="does not exist"):
        evaluator.evaluate_with_evidence(broken)


def test_a_failing_evaluator_process_is_an_error_not_an_abstention(tmp_path):
    manifest = evaluator_manifest(
        adapter_entry=(sys.executable, "-c", "raise SystemExit(3)")
    )
    evaluator = PluginBackedEvaluator(adapter=ProcessAdapter(), manifest=manifest)
    _, request = run_evaluation(tmp_path, "ok")
    with pytest.raises(EvaluationError, match="protected evaluator failed"):
        evaluator.evaluate_with_evidence(request)


# --------------------------------------------------------------------------
# contract-level rules
# --------------------------------------------------------------------------

def test_an_admissible_verdict_needs_at_least_one_metric():
    with pytest.raises(ContractError, match="at least one metric"):
        Verdict(status=VerdictStatus.ADMISSIBLE).validate()


def test_the_verdict_contract_accepts_a_store_key_as_a_source():
    verdict = Verdict(
        status=VerdictStatus.ADMISSIBLE,
        metrics=(Metric(name="area_um2", value=1.0, context={
            "source_artifact_store_key": "report.json",
        }),),
    )
    verdict.validate()


def test_the_verdict_contract_refuses_a_number_with_no_source_at_all():
    with pytest.raises(ContractError, match="cites no source"):
        Verdict(
            status=VerdictStatus.ADMISSIBLE,
            metrics=(Metric(name="area_um2", value=1.0),),
        ).validate()
