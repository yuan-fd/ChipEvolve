"""Offline-only trajectory and shadow-policy baselines for P14.

The policies in this module cannot submit tasks.  They produce a
ShadowPolicyProposal that always has ``execution_allowed=False``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Iterable, Mapping, Sequence

import numpy as np

from openroad_platform_contracts import (
    EvidencePointer,
    LearningObservation,
    ObjectiveSpec,
    ParameterSpec,
    ShadowPolicyProposal,
    TrajectoryStep,
)


def _digest(payload) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def build_trajectory(observations: Sequence[LearningObservation],
                     objectives: Sequence[ObjectiveSpec], *, trajectory_id: str,
                     runtime_scale_seconds: float = 3600.0,
                     failure_penalty: float = -1.0) -> tuple[TrajectoryStep, ...]:
    if len(observations) < 2:
        raise ValueError("Trajectory construction requires at least two observations")
    if runtime_scale_seconds <= 0 or failure_penalty > 0:
        raise ValueError("Invalid trajectory reward configuration")
    for item in observations:
        item.validate()
    for objective in objectives:
        objective.validate()
    context = observations[0].context
    if any(item.context.fingerprint != context.fingerprint for item in observations):
        raise ValueError("Trajectory observations cross a context boundary")
    steps = []
    for index in range(1, len(observations)):
        previous = observations[index - 1]
        current = observations[index]
        state = {name: float(value) for name, value in previous.metrics.items()}
        state["failed"] = 0.0 if previous.status == "succeeded" else 1.0
        next_state = {name: float(value) for name, value in current.metrics.items()}
        next_state["failed"] = 0.0 if current.status == "succeeded" else 1.0
        components = {}
        for objective in objectives:
            old = previous.metrics.get(objective.metric_name)
            new = current.metrics.get(objective.metric_name)
            gain = 0.0
            if isinstance(old, (int, float)) and isinstance(new, (int, float)):
                scale = max(abs(float(old)), 1e-9)
                raw = (float(new) - float(old)) / scale
                gain = raw if objective.direction == "max" else -raw
            components[f"{objective.metric_name}_gain"] = objective.weight * gain
        components["runtime_penalty"] = -float(current.cost_seconds) / runtime_scale_seconds
        components["failure_penalty"] = failure_penalty if current.status != "succeeded" else 0.0
        reward = float(sum(components.values()))
        step = TrajectoryStep(
            trajectory_id=trajectory_id, step_index=index - 1,
            design_id=context.design_id, context_fingerprint=context.fingerprint,
            state=state, action=dict(current.parameters), next_state=next_state,
            reward_components=components, reward=reward,
            terminal=index == len(observations) - 1,
            run_id=current.run_id, attempt_id=current.attempt_id,
            evidence=current.evidence,
        )
        step.validate()
        steps.append(step)
    return tuple(steps)


def split_by_design(steps: Iterable[TrajectoryStep], held_out_designs: set[str]) \
        -> tuple[tuple[TrajectoryStep, ...], tuple[TrajectoryStep, ...]]:
    items = tuple(steps)
    train = tuple(item for item in items if item.design_id not in held_out_designs)
    held_out = tuple(item for item in items if item.design_id in held_out_designs)
    if {item.design_id for item in train} & {item.design_id for item in held_out}:
        raise AssertionError("Design leakage detected")
    return train, held_out


class BehaviorCloningShadowPolicy:
    policy_id = "behavior-cloning-ridge-v1"

    def __init__(self, *, ridge: float = 1e-6):
        if ridge <= 0:
            raise ValueError("ridge must be positive")
        self.ridge = float(ridge)
        self.state_names: tuple[str, ...] = ()
        self.action_names: tuple[str, ...] = ()
        self.coefficients: np.ndarray | None = None
        self.mean_return = 0.0

    def fit(self, steps: Sequence[TrajectoryStep]) -> "BehaviorCloningShadowPolicy":
        if len(steps) < 2:
            raise ValueError("Behavior cloning requires at least two trajectory steps")
        for step in steps:
            step.validate()
        self.state_names = tuple(sorted({name for step in steps for name in step.state}))
        self.action_names = tuple(sorted({name for step in steps for name in step.action}))
        if not self.state_names or not self.action_names:
            raise ValueError("Trajectory lacks state or action features")
        x = np.array([[1.0] + [step.state.get(name, 0.0) for name in self.state_names]
                      for step in steps], dtype=float)
        y = np.array([[step.action.get(name, 0.0) for name in self.action_names]
                      for step in steps], dtype=float)
        regularizer = self.ridge * np.eye(x.shape[1])
        regularizer[0, 0] = 0.0
        self.coefficients = np.linalg.solve(x.T @ x + regularizer, x.T @ y)
        self.mean_return = float(np.mean([step.reward for step in steps]))
        return self

    def propose(self, *, design_id: str, context_fingerprint: str,
                state: Mapping[str, float], evidence: Sequence[EvidencePointer],
                parameter_space: Sequence[ParameterSpec] = ()) -> ShadowPolicyProposal:
        if self.coefficients is None:
            raise ValueError("Behavior cloning policy must be fitted")
        vector = np.array([1.0] + [float(state.get(name, 0.0))
                                  for name in self.state_names], dtype=float)
        predicted = vector @ self.coefficients
        action = {name: float(value) for name, value in zip(self.action_names, predicted)}
        bounds = {item.name: item for item in parameter_space}
        for item in parameter_space:
            item.validate()
        action = {name: min(bounds[name].upper, max(bounds[name].lower, value))
                  if name in bounds else value for name, value in action.items()}
        proposal_seed = _digest({"policy": self.policy_id, "design": design_id,
                                 "context": context_fingerprint,
                                 "state": dict(state), "action": action})[:20]
        proposal = ShadowPolicyProposal(
            proposal_id=f"shadow-{proposal_seed}", policy_id=self.policy_id,
            design_id=design_id, context_fingerprint=context_fingerprint,
            state={key: float(value) for key, value in state.items()}, action=action,
            expected_return=self.mean_return, evidence=tuple(evidence),
        )
        proposal.validate()
        return proposal


class OfflineLinearQShadowPolicy:
    """Fitted linear action-value baseline used only for offline comparison."""

    policy_id = "offline-linear-q-ridge-v1"

    def __init__(self, *, ridge: float = 1e-6):
        if ridge <= 0:
            raise ValueError("ridge must be positive")
        self.ridge = float(ridge)
        self.state_names: tuple[str, ...] = ()
        self.action_names: tuple[str, ...] = ()
        self.coefficients: np.ndarray | None = None

    def fit(self, steps: Sequence[TrajectoryStep]) -> "OfflineLinearQShadowPolicy":
        if len(steps) < 2:
            raise ValueError("Offline Q fitting requires at least two trajectory steps")
        for step in steps:
            step.validate()
        self.state_names = tuple(sorted({name for step in steps for name in step.state}))
        self.action_names = tuple(sorted({name for step in steps for name in step.action}))
        x = np.array([
            [1.0] + [step.state.get(name, 0.0) for name in self.state_names]
            + [step.action.get(name, 0.0) for name in self.action_names]
            for step in steps
        ], dtype=float)
        y = np.array([step.reward for step in steps], dtype=float)
        regularizer = self.ridge * np.eye(x.shape[1])
        regularizer[0, 0] = 0.0
        self.coefficients = np.linalg.solve(x.T @ x + regularizer, x.T @ y)
        return self

    def propose(self, *, design_id: str, context_fingerprint: str,
                state: Mapping[str, float], candidate_actions: Sequence[Mapping[str, float]],
                evidence: Sequence[EvidencePointer]) -> ShadowPolicyProposal:
        if self.coefficients is None:
            raise ValueError("Offline Q policy must be fitted")
        if not candidate_actions or len(candidate_actions) > 4096:
            raise ValueError("Offline Q requires 1-4096 bounded candidate actions")
        scored = []
        for action in candidate_actions:
            if set(action) - set(self.action_names):
                raise ValueError("Candidate action contains an unseen parameter")
            vector = np.array(
                [1.0] + [float(state.get(name, 0.0)) for name in self.state_names]
                + [float(action.get(name, 0.0)) for name in self.action_names], dtype=float,
            )
            scored.append((float(vector @ self.coefficients),
                           {name: float(value) for name, value in action.items()}))
        expected_return, action = max(scored, key=lambda item: (item[0],
                                                                 sorted(item[1].items())))
        proposal_seed = _digest({"policy": self.policy_id, "design": design_id,
                                 "context": context_fingerprint,
                                 "state": dict(state), "action": action})[:20]
        proposal = ShadowPolicyProposal(
            proposal_id=f"shadow-{proposal_seed}", policy_id=self.policy_id,
            design_id=design_id, context_fingerprint=context_fingerprint,
            state={key: float(value) for key, value in state.items()}, action=action,
            expected_return=expected_return, evidence=tuple(evidence),
        )
        proposal.validate()
        return proposal
