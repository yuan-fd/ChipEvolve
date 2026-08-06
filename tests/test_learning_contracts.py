from __future__ import annotations

import dataclasses

import pytest

from openroad_platform_contracts import (
    EvidencePointer,
    LearningContext,
    LearningObservation,
    ObjectiveSpec,
    OptimizationStudy,
    OptimizerProposal,
    ParameterSpec,
    Prediction,
    TrajectoryStep,
)


SHA = "a" * 64


def context() -> LearningContext:
    return LearningContext(
        design_id="gcd", design_fingerprint=SHA, platform="nangate45",
        pdk_id="nangate45-public", toolchain_id="orfs-51ad123",
        flow_stage="finish", metric_parser_version="orfs-stage-json-1",
    )


def evidence() -> EvidencePointer:
    return EvidencePointer(ref="artifact:run-result", sha256="b" * 64)


def observation() -> LearningObservation:
    return LearningObservation(
        observation_id="obs-1", context=context(),
        parameters={"core_utilization_pct": 35.0, "place_density": 0.5},
        metrics={"area_um2": 123.0, "wns_ns": -0.1},
        metric_units={"area_um2": "um2", "wns_ns": "ns"},
        status="succeeded", cost_seconds=12.5, run_id="run-1",
        attempt_id="attempt-1", evidence=(evidence(),),
    )


def study() -> OptimizationStudy:
    return OptimizationStudy(
        study_id="study-1", design_id="gcd", context_fingerprint=context().fingerprint,
        parameter_space=(ParameterSpec("core_utilization_pct", 20, 60),
                         ParameterSpec("place_density", 0.3, 0.8)),
        objectives=(ObjectiveSpec("area_um2", "min"),
                    ObjectiveSpec("wns_ns", "max")),
        max_runs=12, seed=7,
    )


def test_learning_contracts_round_trip_and_separate_observed_from_predicted():
    value = observation()
    assert LearningObservation.from_dict(value.to_dict()) == value
    assert value.source == "observed"
    assert len(value.fingerprint) == 64

    prediction = Prediction(
        prediction_id="prediction-1", study_id="study-1", candidate_id="candidate-1",
        metric_name="area_um2", mean=120, stddev=4, model_id="gp-rbf-1",
        context_fingerprint=context().fingerprint,
    )
    assert Prediction.from_dict(prediction.to_dict()).source == "predicted"
    with pytest.raises(ValueError, match="source must be predicted"):
        dataclasses.replace(prediction, source="observed").validate()
    with pytest.raises(ValueError, match="source must be observed"):
        dataclasses.replace(value, source="predicted").validate()


def test_optimizer_proposal_is_non_executable_and_versioned():
    current = study()
    current.validate()
    prediction = Prediction(
        prediction_id="prediction-1", study_id=current.study_id,
        candidate_id="candidate-1", metric_name="area_um2", mean=120,
        stddev=4, model_id="gp-rbf-1",
        context_fingerprint=current.context_fingerprint,
    )
    proposal = OptimizerProposal(
        proposal_id="proposal-1", study_id=current.study_id,
        candidate_id="candidate-1", iteration=2,
        parameters={"core_utilization_pct": 32.0, "place_density": 0.48},
        predictions=(prediction,), acquisition_value=0.72, evidence=(evidence(),),
    )
    assert OptimizerProposal.from_dict(proposal.to_dict()) == proposal
    with pytest.raises(ValueError, match="cannot execute"):
        dataclasses.replace(proposal, execution_allowed=True).validate()
    malformed = proposal.to_dict()
    malformed["unknown"] = True
    with pytest.raises(ValueError, match="Unknown OptimizerProposal"):
        OptimizerProposal.from_dict(malformed)


def test_trajectory_is_offline_evidence_and_context_bound():
    step = TrajectoryStep(
        trajectory_id="trajectory-1", step_index=0, design_id="gcd",
        context_fingerprint=context().fingerprint,
        state={"area_um2": 130.0}, action={"core_utilization_pct": 35.0},
        next_state={"area_um2": 123.0},
        reward_components={"area_gain": 7.0, "runtime_penalty": -0.5},
        reward=6.5, terminal=True, run_id="run-1", attempt_id="attempt-1",
        evidence=(evidence(),),
    )
    assert TrajectoryStep.from_dict(step.to_dict()) == step
    with pytest.raises(ValueError, match="cannot execute"):
        dataclasses.replace(step, execution_allowed=True).validate()
