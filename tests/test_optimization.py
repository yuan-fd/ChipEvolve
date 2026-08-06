from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from openroad_platform_analysis import (
    GaussianProcessRegressorLite,
    MultiObjectiveBayesianOptimizer,
    OptimizationStudyStore,
    pareto_front,
    proposal_to_experiment_plan,
)
from openroad_platform_contracts import (
    EvidencePointer,
    LearningContext,
    LearningObservation,
    ObjectiveSpec,
    OptimizationStudy,
    ParameterSpec,
)


def _context(design="gcd"):
    return LearningContext(
        design_id=design, design_fingerprint="a" * 64, platform="nangate45",
        pdk_id="nangate45-public", toolchain_id="orfs-51ad123",
        flow_stage="finish", metric_parser_version="orfs-stage-json-1",
    )


def _study(max_runs=12):
    return OptimizationStudy(
        study_id="study-gcd", design_id="gcd",
        context_fingerprint=_context().fingerprint,
        parameter_space=(ParameterSpec("core_utilization_pct", 20.0, 60.0),
                         ParameterSpec("place_density", 0.3, 0.8)),
        objectives=(ObjectiveSpec("area_um2", "min"),
                    ObjectiveSpec("wns_ns", "max")),
        max_runs=max_runs, seed=19,
    )


def _observation(index, util, density, area, wns):
    return LearningObservation(
        observation_id=f"observation-{index}", context=_context(),
        parameters={"core_utilization_pct": util, "place_density": density},
        metrics={"area_um2": area, "wns_ns": wns},
        metric_units={"area_um2": "um2", "wns_ns": "ns"},
        status="succeeded", cost_seconds=10 + index,
        run_id=f"run-{index}", attempt_id=f"attempt-{index}",
        evidence=(EvidencePointer(ref=f"artifact:result-{index}",
                                  sha256=f"{index + 1:x}" * 64),),
    )


def _observations():
    return [
        _observation(0, 25, 0.35, 150, -0.20),
        _observation(1, 30, 0.42, 135, -0.10),
        _observation(2, 35, 0.48, 125, -0.04),
        _observation(3, 40, 0.55, 119, -0.08),
        _observation(4, 45, 0.62, 116, -0.18),
    ]


def test_numpy_gp_returns_mean_and_nonnegative_uncertainty():
    x = np.array([[0.0], [0.5], [1.0]])
    y = np.array([1.0, 0.0, 1.0])
    gp = GaussianProcessRegressorLite().fit(x, y)
    mean, stddev = gp.predict(np.array([[0.5], [0.75]]))
    assert mean.shape == stddev.shape == (2,)
    assert np.isfinite(mean).all()
    assert (stddev >= 0).all()
    assert abs(mean[0]) < 0.01


def test_bo_proposal_is_deterministic_bounded_predicted_only_and_plan_data():
    study = _study()
    observations = _observations()
    optimizer = MultiObjectiveBayesianOptimizer(pool_size=256)
    first = optimizer.propose(study, observations)
    second = optimizer.propose(study, observations)
    assert first == second
    assert 20 <= first.parameters["core_utilization_pct"] <= 60
    assert 0.3 <= first.parameters["place_density"] <= 0.8
    assert {item.metric_name for item in first.predictions} == {"area_um2", "wns_ns"}
    assert all(item.source == "predicted" and item.stddev >= 0
               for item in first.predictions)
    assert first.execution_allowed is False

    plan = proposal_to_experiment_plan(first, study,
                                       baseline_parameters={"clock_period_ns": 10.0})
    assert plan.candidates[0].parameters == first.parameters
    assert plan.provenance["predictions_are_canonical_metrics"] is False


def test_study_store_is_idempotent_context_bound_and_reports_pareto(tmp_path):
    study = _study(max_runs=8)
    store = OptimizationStudyStore(tmp_path / "study.db")
    store.create(study)
    store.create(study)
    for observation in _observations():
        store.add_observation(study.study_id, observation)
    optimizer = MultiObjectiveBayesianOptimizer(pool_size=128)
    proposal = optimizer.propose(study, store.observations(study.study_id))
    store.save_proposal(proposal)
    store.save_proposal(proposal)
    description = store.describe(study.study_id)
    assert description["observation_source"] == "observed"
    assert description["prediction_source"] == "predicted"
    assert description["pareto_observation_ids"]
    assert store.proposals(study.study_id) == [proposal]

    wrong_context = dataclasses.replace(_observations()[0], context=_context("aes"))
    with pytest.raises(ValueError, match="context"):
        store.add_observation(study.study_id, wrong_context)


def test_pareto_front_keeps_tradeoffs_and_removes_dominated():
    observations = _observations()
    front = pareto_front(_study().objectives, observations)
    assert "observation-0" not in front
    assert "observation-2" in front
    assert "observation-4" in front
