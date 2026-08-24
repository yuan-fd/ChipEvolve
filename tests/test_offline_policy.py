from __future__ import annotations

import dataclasses

import pytest

from openroad_platform_analysis import (
    BehaviorCloningShadowPolicy,
    OfflineInteractionQShadowPolicy,
    OfflineLinearQShadowPolicy,
    build_trajectory,
    split_by_design,
)
from openroad_platform_contracts import (
    EvidencePointer,
    LearningContext,
    LearningObservation,
    ObjectiveSpec,
    OptimizationStudy,
    ParameterSpec,
    ShadowPolicyProposal,
    TrajectoryStep,
)

def _context():
    return LearningContext(
        design_id="gcd", design_fingerprint="a" * 64, platform="nangate45",
        pdk_id="nangate45-public", toolchain_id="orfs-51ad123",
        flow_stage="finish", metric_parser_version="orfs-stage-json-1",
    )


def _observation(index, util, density, area, wns, cost=10):
    return LearningObservation(
        observation_id=f"obs-{index}", context=_context(),
        parameters={"core_utilization_pct": util, "place_density": density},
        metrics={"area_um2": area, "wns_ns": wns},
        metric_units={"area_um2": "um2", "wns_ns": "ns"},
        status="succeeded", cost_seconds=cost, run_id=f"run-{index}",
        attempt_id=f"attempt-{index}",
        evidence=(EvidencePointer(ref=f"artifact:result-{index}",
                                  sha256=f"{index + 1:x}" * 64),),
    )


def _observations():
    return (
        _observation(0, 25, 0.35, 150, -0.20),
        _observation(1, 30, 0.42, 135, -0.10),
        _observation(2, 35, 0.48, 125, -0.04),
        _observation(3, 40, 0.55, 119, -0.08),
        _observation(4, 45, 0.62, 116, -0.18),
    )


def _steps():
    return build_trajectory(
        _observations(),
        (ObjectiveSpec("area_um2", "min"), ObjectiveSpec("wns_ns", "max")),
        trajectory_id="trajectory-gcd", runtime_scale_seconds=1000,
    )


def test_real_observations_become_versioned_reward_trajectory():
    steps = _steps()
    assert len(steps) == 4
    assert steps[-1].terminal is True
    assert all(step.execution_allowed is False for step in steps)
    assert steps[0].reward_components["area_um2_gain"] > 0
    assert steps[0].reward_components["wns_ns_gain"] > 0
    assert TrajectoryStepRoundTrip(steps[0]) == steps[0]


def TrajectoryStepRoundTrip(step):
    from openroad_platform_contracts import TrajectoryStep
    return TrajectoryStep.from_dict(step.to_dict())


def test_behavior_cloning_and_offline_q_only_emit_shadow_actions():
    steps = _steps()
    evidence = steps[-1].evidence
    state = steps[-1].next_state
    bounds = (ParameterSpec("core_utilization_pct", 20, 60),
              ParameterSpec("place_density", 0.3, 0.8))
    behavior = BehaviorCloningShadowPolicy().fit(steps)
    behavior_proposal = behavior.propose(
        design_id="gcd", context_fingerprint=_context().fingerprint,
        state=state, evidence=evidence, parameter_space=bounds,
    )
    assert behavior_proposal.execution_allowed is False
    assert 20 <= behavior_proposal.action["core_utilization_pct"] <= 60
    assert ShadowPolicyProposal.from_dict(behavior_proposal.to_dict()) == behavior_proposal

    q_policy = OfflineLinearQShadowPolicy().fit(steps)
    q_proposal = q_policy.propose(
        design_id="gcd", context_fingerprint=_context().fingerprint,
        state=state, candidate_actions=(
            {"core_utilization_pct": 30, "place_density": 0.4},
            {"core_utilization_pct": 42, "place_density": 0.58},
        ), evidence=evidence,
    )
    assert q_proposal.execution_allowed is False
    assert q_proposal.action in (
        {"core_utilization_pct": 30.0, "place_density": 0.4},
        {"core_utilization_pct": 42.0, "place_density": 0.58},
    )
    with pytest.raises(ValueError, match="cannot execute"):
        dataclasses.replace(q_proposal, execution_allowed=True).validate()


def test_design_level_split_has_no_leakage():
    gcd_steps = _steps()
    aes_steps = tuple(dataclasses.replace(
        step, design_id="aes", context_fingerprint="c" * 64,
        trajectory_id="trajectory-aes",
    ) for step in gcd_steps)
    train, held_out = split_by_design(gcd_steps + aes_steps, {"aes"})
    assert {step.design_id for step in train} == {"gcd"}
    assert {step.design_id for step in held_out} == {"aes"}


def test_interaction_shadow_policy_can_rank_a_compound_parameter_condition():
    """A-only and B-only have no reward; A+B is useful.

    This is the minimum regression for the prior single/linear-parameter blind
    spot.  The proposal remains a non-executable shadow recommendation.
    """
    evidence = (EvidencePointer(ref="artifact:interaction-study", sha256="f" * 64),)
    rows = ((0, 0, 0.0), (0, 1, 0.0), (1, 0, 0.0), (1, 1, 10.0))
    steps = tuple(TrajectoryStep(
        trajectory_id="interaction-study", step_index=index, design_id="gcd",
        context_fingerprint=_context().fingerprint, state={"congestion": 0.0},
        action={"core_utilization_pct": float(util), "place_density": float(density)},
        next_state={"congestion": 0.0}, reward_components={"observed": reward},
        reward=reward, terminal=index == len(rows) - 1,
        run_id=f"interaction-run-{index}", attempt_id=f"interaction-attempt-{index}", evidence=evidence,
    ) for index, (util, density, reward) in enumerate(rows))
    policy = OfflineInteractionQShadowPolicy().fit(steps)
    proposal = policy.propose(
        design_id="gcd", context_fingerprint=_context().fingerprint,
        state={"congestion": 0.0}, evidence=evidence,
        candidate_actions=(
            {"core_utilization_pct": 1.0, "place_density": 0.0},
            {"core_utilization_pct": 0.0, "place_density": 1.0},
            {"core_utilization_pct": 1.0, "place_density": 1.0},
        ),
    )
    assert proposal.action == {"core_utilization_pct": 1.0, "place_density": 1.0}
    assert proposal.expected_return > 9.9
    assert proposal.execution_allowed is False
