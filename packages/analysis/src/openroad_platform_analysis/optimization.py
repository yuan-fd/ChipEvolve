"""Deterministic NumPy GP/BO proposals and durable study metadata.

The optimizer never imports Runtime or launches a process.  Its only executable
handoff is a versioned ExperimentPlan consumed by the scheduler policy layer.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import sqlite3
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from openroad_platform_contracts import (
    EvidencePointer,
    ExperimentCandidate,
    ExperimentPlan,
    LearningObservation,
    ObjectiveSpec,
    OptimizationStudy,
    OptimizerProposal,
    Prediction,
)


def _digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _normal_pdf(value: np.ndarray) -> np.ndarray:
    return np.exp(-0.5 * value * value) / math.sqrt(2 * math.pi)


def _normal_cdf(value: np.ndarray) -> np.ndarray:
    # Abramowitz-Stegun 7.1.26; avoids a SciPy dependency on ARM64.
    absolute = np.abs(value)
    t = 1.0 / (1.0 + 0.2316419 * absolute)
    polynomial = t * (0.319381530 + t * (-0.356563782 + t * (
        1.781477937 + t * (-1.821255978 + t * 1.330274429))))
    approximation = 1.0 - _normal_pdf(absolute) * polynomial
    return np.where(value >= 0, approximation, 1.0 - approximation)


class GaussianProcessRegressorLite:
    """Small exact RBF GP suitable for bounded platform experiments."""

    model_id = "gp-rbf-numpy-v1"

    def __init__(self, *, length_scale: float = 0.35, noise: float = 1e-6):
        if length_scale <= 0 or noise <= 0:
            raise ValueError("GP length_scale and noise must be positive")
        self.length_scale = float(length_scale)
        self.noise = float(noise)
        self._x: np.ndarray | None = None
        self._mean = 0.0
        self._scale = 1.0
        self._cholesky: np.ndarray | None = None
        self._alpha: np.ndarray | None = None

    def fit(self, x: np.ndarray, y: np.ndarray) -> "GaussianProcessRegressorLite":
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float).reshape(-1)
        if x.ndim != 2 or len(x) != len(y) or len(x) < 1:
            raise ValueError("GP requires aligned non-empty X and y")
        if not np.isfinite(x).all() or not np.isfinite(y).all():
            raise ValueError("GP inputs must be finite")
        self._x = x
        self._mean = float(y.mean())
        standard = float(y.std())
        self._scale = standard if standard > 1e-12 else 1.0
        normalized = (y - self._mean) / self._scale
        kernel = self._kernel(x, x)
        jitter = self.noise
        for _ in range(8):
            try:
                self._cholesky = np.linalg.cholesky(kernel + jitter * np.eye(len(x)))
                break
            except np.linalg.LinAlgError:
                jitter *= 10
        if self._cholesky is None:
            raise ValueError("GP kernel is not positive definite")
        self._alpha = np.linalg.solve(
            self._cholesky.T, np.linalg.solve(self._cholesky, normalized),
        )
        return self

    def predict(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if self._x is None or self._cholesky is None or self._alpha is None:
            raise ValueError("GP must be fitted before prediction")
        x = np.asarray(x, dtype=float)
        if x.ndim != 2 or x.shape[1] != self._x.shape[1]:
            raise ValueError("GP prediction dimensionality mismatch")
        cross = self._kernel(self._x, x)
        normalized_mean = cross.T @ self._alpha
        solved = np.linalg.solve(self._cholesky, cross)
        normalized_variance = np.maximum(1e-12, 1.0 - np.sum(solved * solved, axis=0))
        return (self._mean + self._scale * normalized_mean,
                self._scale * np.sqrt(normalized_variance))

    def _kernel(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        distance = np.sum((left[:, None, :] - right[None, :, :]) ** 2, axis=2)
        return np.exp(-0.5 * distance / (self.length_scale ** 2))


class MultiObjectiveBayesianOptimizer:
    """Budgeted proposal generator with per-objective GPs and scalarized EI."""

    def __init__(self, *, pool_size: int = 512, exploration: float = 0.05):
        if not 32 <= pool_size <= 8192 or exploration < 0:
            raise ValueError("Invalid BO pool_size or exploration")
        self.pool_size = int(pool_size)
        self.exploration = float(exploration)

    def propose(self, study: OptimizationStudy,
                observations: Iterable[LearningObservation]) -> OptimizerProposal:
        study.validate()
        items = tuple(observations)
        if len(items) >= study.max_runs:
            raise ValueError("Optimization study run budget is exhausted")
        for item in items:
            item.validate()
            if item.context.fingerprint != study.context_fingerprint:
                raise ValueError("Observation context does not match study")
            if item.context.design_id != study.design_id:
                raise ValueError("Observation design does not match study")
        parameter_names = [item.name for item in study.parameter_space]
        lows = np.array([item.lower for item in study.parameter_space], dtype=float)
        highs = np.array([item.upper for item in study.parameter_space], dtype=float)
        iteration = len(items)
        rng = np.random.default_rng(study.seed + iteration * 1009)
        pool = rng.random((self.pool_size, len(parameter_names)))
        existing = np.array([
            [(item.parameters[name] - low) / (high - low)
             for name, low, high in zip(parameter_names, lows, highs)]
            for item in items if all(name in item.parameters for name in parameter_names)
        ], dtype=float)
        if existing.size:
            keep = np.ones(len(pool), dtype=bool)
            for row in existing:
                keep &= np.max(np.abs(pool - row), axis=1) > 1e-8
            pool = pool[keep]
        if not len(pool):
            raise ValueError("No unobserved candidate remains in the proposal pool")

        complete = [item for item in items if item.status == "succeeded" and all(
            objective.metric_name in item.metrics for objective in study.objectives
        ) and all(name in item.parameters for name in parameter_names)]
        predictions_by_metric: dict[str, tuple[np.ndarray, np.ndarray, str]] = {}
        if len(complete) >= 2:
            train_x = np.array([
                [(item.parameters[name] - low) / (high - low)
                 for name, low, high in zip(parameter_names, lows, highs)]
                for item in complete
            ], dtype=float)
            utility_mean = np.zeros(len(pool), dtype=float)
            utility_variance = np.zeros(len(pool), dtype=float)
            observed_utility = np.zeros(len(complete), dtype=float)
            total_weight = sum(objective.weight for objective in study.objectives)
            for objective in study.objectives:
                values = np.array([item.metrics[objective.metric_name]
                                   for item in complete], dtype=float)
                gp = GaussianProcessRegressorLite().fit(train_x, values)
                means, stddevs = gp.predict(pool)
                predictions_by_metric[objective.metric_name] = (means, stddevs, gp.model_id)
                minimum, maximum = float(values.min()), float(values.max())
                scale = maximum - minimum if maximum - minimum > 1e-12 else 1.0
                if objective.direction == "max":
                    normalized_mean = (means - minimum) / scale
                    normalized_observed = (values - minimum) / scale
                else:
                    normalized_mean = (maximum - means) / scale
                    normalized_observed = (maximum - values) / scale
                weight = objective.weight / total_weight
                utility_mean += weight * normalized_mean
                utility_variance += (weight * stddevs / scale) ** 2
                observed_utility += weight * normalized_observed
            utility_stddev = np.sqrt(np.maximum(utility_variance, 1e-12))
            improvement = utility_mean - float(observed_utility.max()) - self.exploration
            z_value = improvement / utility_stddev
            acquisition = improvement * _normal_cdf(z_value) \
                + utility_stddev * _normal_pdf(z_value)
            chosen_index = int(np.argmax(acquisition))
            acquisition_value = float(acquisition[chosen_index])
        else:
            chosen_index = 0
            acquisition_value = 0.0

        normalized_candidate = pool[chosen_index]
        values = lows + normalized_candidate * (highs - lows)
        parameters = {name: float(value) for name, value in zip(parameter_names, values)}
        candidate_seed = _digest({"study": study.study_id, "iteration": iteration,
                                  "parameters": parameters})[:20]
        candidate_id = f"candidate-{candidate_seed}"
        prediction_records = []
        for objective in study.objectives:
            if objective.metric_name not in predictions_by_metric:
                continue
            means, stddevs, model_id = predictions_by_metric[objective.metric_name]
            prediction_records.append(Prediction(
                prediction_id=f"prediction-{_digest({'candidate': candidate_id, 'metric': objective.metric_name})[:20]}",
                study_id=study.study_id, candidate_id=candidate_id,
                metric_name=objective.metric_name, mean=float(means[chosen_index]),
                stddev=float(stddevs[chosen_index]), model_id=model_id,
                context_fingerprint=study.context_fingerprint,
            ))
        evidence = [EvidencePointer(
            ref=f"source:study:{study.study_id}", sha256=_digest(study.to_dict()),
        )]
        seen = {evidence[0].ref}
        for item in items:
            for pointer in item.evidence:
                if pointer.ref not in seen:
                    evidence.append(pointer)
                    seen.add(pointer.ref)
                    break
        proposal_seed = _digest({"study": study.study_id, "iteration": iteration,
                                 "candidate": candidate_id,
                                 "observations": [item.fingerprint for item in items]})[:20]
        proposal = OptimizerProposal(
            proposal_id=f"optimizer-{proposal_seed}", study_id=study.study_id,
            candidate_id=candidate_id, iteration=iteration, parameters=parameters,
            predictions=tuple(prediction_records), acquisition_value=acquisition_value,
            evidence=tuple(evidence),
        )
        proposal.validate()
        return proposal


class OptimizationStudyStore:
    """Durable study/proposal metadata; observed data remains immutable."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS optimization_studies_v1 (
                    study_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS study_observations_v1 (
                    study_id TEXT NOT NULL,
                    observation_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    PRIMARY KEY(study_id, observation_id),
                    UNIQUE(study_id, sequence),
                    FOREIGN KEY(study_id) REFERENCES optimization_studies_v1(study_id)
                );
                CREATE TABLE IF NOT EXISTS optimizer_proposals_v1 (
                    proposal_id TEXT PRIMARY KEY,
                    study_id TEXT NOT NULL,
                    iteration INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(study_id, iteration),
                    FOREIGN KEY(study_id) REFERENCES optimization_studies_v1(study_id)
                );
            """)

    def create(self, study: OptimizationStudy) -> str:
        study.validate()
        payload = study.to_dict()
        fingerprint = _digest(payload)
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO optimization_studies_v1 VALUES (?, ?, ?, datetime('now'))",
                    (study.study_id, json.dumps(payload, ensure_ascii=False), fingerprint),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    "SELECT fingerprint FROM optimization_studies_v1 WHERE study_id = ?",
                    (study.study_id,),
                ).fetchone()
                if row is None or row["fingerprint"] != fingerprint:
                    raise ValueError("Study identity conflicts with existing definition")
        return study.study_id

    def get(self, study_id: str) -> OptimizationStudy:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM optimization_studies_v1 WHERE study_id = ?",
                (study_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown optimization study: {study_id}")
        return OptimizationStudy.from_dict(json.loads(row["payload_json"]))

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT s.study_id, s.payload_json,
                          (SELECT COUNT(*) FROM study_observations_v1 o
                           WHERE o.study_id = s.study_id) AS observation_count,
                          (SELECT COUNT(*) FROM optimizer_proposals_v1 p
                           WHERE p.study_id = s.study_id) AS proposal_count
                   FROM optimization_studies_v1 s
                   ORDER BY s.created_at DESC LIMIT ?""", (limit,),
            ).fetchall()
        result = []
        for row in rows:
            study = OptimizationStudy.from_dict(json.loads(row["payload_json"]))
            result.append({"study_id": study.study_id, "design_id": study.design_id,
                           "status": study.status, "max_runs": study.max_runs,
                           "observation_count": row["observation_count"],
                           "proposal_count": row["proposal_count"],
                           "context_fingerprint": study.context_fingerprint})
        return result

    def add_observation(self, study_id: str, observation: LearningObservation) -> str:
        study = self.get(study_id)
        observation.validate()
        if observation.context.fingerprint != study.context_fingerprint:
            raise ValueError("Observation context does not match study")
        with self._connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM study_observations_v1 WHERE study_id = ?",
                (study_id,),
            ).fetchone()[0]
            if count >= study.max_runs:
                raise ValueError("Study run budget is exhausted")
            payload = observation.to_dict()
            try:
                connection.execute(
                    "INSERT INTO study_observations_v1 VALUES (?, ?, ?, ?, ?)",
                    (study_id, observation.observation_id,
                     json.dumps(payload, ensure_ascii=False), observation.fingerprint,
                     count),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    """SELECT fingerprint FROM study_observations_v1
                       WHERE study_id = ? AND observation_id = ?""",
                    (study_id, observation.observation_id),
                ).fetchone()
                if row is None or row["fingerprint"] != observation.fingerprint:
                    raise ValueError("Study observation conflicts with existing evidence")
        return observation.observation_id

    def observations(self, study_id: str) -> list[LearningObservation]:
        self.get(study_id)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT payload_json FROM study_observations_v1
                   WHERE study_id = ? ORDER BY sequence""", (study_id,),
            ).fetchall()
        return [LearningObservation.from_dict(json.loads(row["payload_json"])) for row in rows]

    def save_proposal(self, proposal: OptimizerProposal) -> str:
        self.get(proposal.study_id)
        proposal.validate()
        payload = proposal.to_dict()
        fingerprint = _digest(payload)
        with self._connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO optimizer_proposals_v1
                       VALUES (?, ?, ?, ?, ?, datetime('now'))""",
                    (proposal.proposal_id, proposal.study_id, proposal.iteration,
                     json.dumps(payload, ensure_ascii=False), fingerprint),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    """SELECT fingerprint FROM optimizer_proposals_v1
                       WHERE study_id = ? AND iteration = ?""",
                    (proposal.study_id, proposal.iteration),
                ).fetchone()
                if row is None or row["fingerprint"] != fingerprint:
                    raise ValueError("Study iteration already has a different proposal")
        return proposal.proposal_id

    def proposals(self, study_id: str) -> list[OptimizerProposal]:
        self.get(study_id)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT payload_json FROM optimizer_proposals_v1
                   WHERE study_id = ? ORDER BY iteration""", (study_id,),
            ).fetchall()
        return [OptimizerProposal.from_dict(json.loads(row["payload_json"])) for row in rows]

    def describe(self, study_id: str) -> dict[str, Any]:
        study = self.get(study_id)
        observations = self.observations(study_id)
        proposals = self.proposals(study_id)
        return {"study": study.to_dict(),
                "observations": [item.to_dict() for item in observations],
                "proposals": [item.to_dict() for item in proposals],
                "pareto_observation_ids": pareto_front(study.objectives, observations),
                "prediction_source": "predicted", "observation_source": "observed"}

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def pareto_front(objectives: tuple[ObjectiveSpec, ...],
                 observations: Iterable[LearningObservation]) -> list[str]:
    complete = [item for item in observations if item.status == "succeeded" and all(
        objective.metric_name in item.metrics for objective in objectives
    )]
    result = []
    for candidate in complete:
        dominated = False
        for other in complete:
            if other is candidate:
                continue
            no_worse = []
            strictly_better = []
            for objective in objectives:
                left = other.metrics[objective.metric_name]
                right = candidate.metrics[objective.metric_name]
                if objective.direction == "min":
                    no_worse.append(left <= right)
                    strictly_better.append(left < right)
                else:
                    no_worse.append(left >= right)
                    strictly_better.append(left > right)
            if all(no_worse) and any(strictly_better):
                dominated = True
                break
        if not dominated:
            result.append(candidate.observation_id)
    return result


def proposal_to_experiment_plan(proposal: OptimizerProposal, study: OptimizationStudy,
                                *, baseline_parameters: dict[str, Any] | None = None) -> ExperimentPlan:
    proposal.validate()
    study.validate()
    if proposal.study_id != study.study_id:
        raise ValueError("Proposal does not belong to study")
    plan_id = f"plan-{_digest({'proposal': proposal.proposal_id})[:20]}"
    candidate = ExperimentCandidate(
        candidate_id=proposal.candidate_id, parameters=dict(proposal.parameters),
        source_trial_id=proposal.proposal_id,
        evidence_refs=tuple(item.ref for item in proposal.evidence),
    )
    plan = ExperimentPlan(
        plan_id=plan_id, producer="p14-bo-gp", design_id=study.design_id,
        platform="openroad", baseline_parameters=baseline_parameters or {},
        candidates=(candidate,), max_child_runs=1,
        provenance={"study_id": study.study_id,
                    "optimizer_proposal": proposal.to_dict(),
                    "predictions_are_canonical_metrics": False},
    )
    plan.validate()
    return plan
