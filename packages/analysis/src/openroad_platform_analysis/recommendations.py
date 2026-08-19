"""Human-controlled optimization recommendations and bounded automation gates."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from openroad_platform_contracts import (
    LearningObservation, OptimizationStudy, OptimizerProposal,
)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class ConfidenceBreakdown:
    context_match: float
    data_coverage: float
    calibration: float
    safety: float
    evidence_quality: float
    overall: float
    sample_count: int
    nearby_sample_count: int
    held_out_error: float | None
    interval_coverage: float | None
    ood: bool
    reasons: tuple[str, ...]

    def validate(self) -> None:
        for name in ("context_match", "data_coverage", "calibration", "safety",
                     "evidence_quality", "overall"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not 0 <= float(value) <= 1:
                raise ValueError(f"Invalid confidence component: {name}")
        if self.sample_count < 0 or self.nearby_sample_count < 0:
            raise ValueError("Invalid confidence sample count")


@dataclass(frozen=True)
class PolicyRecommendation:
    recommendation_id: str
    study_id: str
    proposal_id: str
    policy_kind: str
    parameters: dict[str, float]
    rationale: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    confidence: ConfidenceBreakdown
    worst_case_cost_seconds: float
    permission_tier: str = "T1"
    execution_allowed: bool = False

    def validate(self) -> None:
        if self.policy_kind not in {"bo-gp", "rl-shadow", "hybrid"}:
            raise ValueError("Invalid recommendation policy kind")
        if self.permission_tier != "T1" or self.execution_allowed:
            raise ValueError("Recommendations are advice and cannot execute")
        if not self.parameters or not self.evidence_refs or self.worst_case_cost_seconds < 0:
            raise ValueError("Incomplete recommendation")
        self.confidence.validate()

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class UserDecision:
    decision_id: str
    recommendation_id: str
    owner_id: str
    action: str
    selected_parameters: dict[str, float]
    comment: str
    recommendation_fingerprint: str
    execution_requested: bool = False

    def validate(self) -> None:
        if self.action not in {"accepted", "modified", "rejected"}:
            raise ValueError("Decision must accept, modify or reject")
        if self.action == "rejected" and self.selected_parameters:
            raise ValueError("Rejected recommendations cannot select parameters")
        if self.action != "rejected" and not self.selected_parameters:
            raise ValueError("Accepted recommendations require parameters")
        if self.execution_requested:
            raise ValueError("A decision cannot directly execute EDA")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class AutomationEnvelope:
    envelope_id: str
    recommendation_id: str
    study_id: str
    eligible: bool
    status: str
    checks: dict[str, bool]
    maximum_candidates: int = 1
    execution_allowed: bool = False

    def validate(self) -> None:
        if self.status not in {"eligible", "not_eligible"}:
            raise ValueError("Invalid automation status")
        if self.eligible != all(self.checks.values()) or (self.eligible != (self.status == "eligible")):
            raise ValueError("Automation eligibility does not match checks")
        if self.maximum_candidates != 1 or self.execution_allowed:
            raise ValueError("Envelope is bounded data and cannot execute")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return dataclasses.asdict(self)


def build_recommendation(study: OptimizationStudy, proposal: OptimizerProposal,
                         observations: Sequence[LearningObservation], *,
                         held_out_error: float | None = None,
                         interval_coverage: float | None = None,
                         worst_case_cost_seconds: float = 7200.0) -> PolicyRecommendation:
    study.validate()
    proposal.validate()
    if proposal.study_id != study.study_id:
        raise ValueError("Proposal does not belong to study")
    if any(item.context.fingerprint != study.context_fingerprint for item in observations):
        raise ValueError("Recommendation observations cross a context boundary")
    bounds = {item.name: item for item in study.parameter_space}
    unknown = set(proposal.parameters) - set(bounds)
    if unknown:
        raise ValueError("Proposal contains an unknown parameter")
    bounded = all(bounds[name].lower <= value <= bounds[name].upper
                  for name, value in proposal.parameters.items())
    distances = []
    for item in observations:
        squares = []
        for name, value in proposal.parameters.items():
            spec = bounds[name]
            width = max(spec.upper - spec.lower, 1e-12)
            if name not in item.parameters:
                squares.append(4.0)
            else:
                squares.append(((item.parameters[name] - value) / width) ** 2)
        distances.append(math.sqrt(sum(squares)))
    nearby = sum(value <= 0.25 for value in distances)
    sample_count = len(observations)
    coverage = min(1.0, sample_count / 20.0) * min(1.0, nearby / 5.0)
    calibrated = (held_out_error is not None and held_out_error <= 0.15
                  and interval_coverage is not None and interval_coverage >= 0.8)
    calibration = 1.0 if calibrated else 0.0
    evidence_quality = (sum(1 for item in observations if item.source == "observed"
                            and item.evidence) / sample_count) if sample_count else 0.0
    ood = not bounded or not distances or min(distances) > 0.5
    components = [1.0, coverage, calibration, 1.0 if bounded and not ood else 0.0,
                  evidence_quality]
    overall = min(components)
    reasons = []
    if sample_count < 20:
        reasons.append(f"数据不足：{sample_count}/20 条同上下文实测")
    if nearby < 5:
        reasons.append(f"候选附近覆盖不足：{nearby}/5 条")
    if not calibrated:
        reasons.append("held-out 校准尚未通过")
    if ood:
        reasons.append("候选超出已观测分布")
    if not reasons:
        reasons.append("上下文、覆盖、校准和安全约束均通过")
    confidence = ConfidenceBreakdown(
        context_match=1.0, data_coverage=coverage, calibration=calibration,
        safety=1.0 if bounded and not ood else 0.0, evidence_quality=evidence_quality,
        overall=overall, sample_count=sample_count, nearby_sample_count=nearby,
        held_out_error=held_out_error, interval_coverage=interval_coverage,
        ood=ood, reasons=tuple(reasons),
    )
    seed = _digest({"study": study.study_id, "proposal": proposal.proposal_id,
                    "observations": [item.fingerprint for item in observations]})[:24]
    result = PolicyRecommendation(
        recommendation_id=f"recommendation-{seed}", study_id=study.study_id,
        proposal_id=proposal.proposal_id, policy_kind="bo-gp",
        parameters=dict(proposal.parameters), rationale=tuple(reasons),
        evidence_refs=tuple(item.ref for item in proposal.evidence),
        confidence=confidence, worst_case_cost_seconds=worst_case_cost_seconds,
    )
    result.validate()
    return result


class RecommendationStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS policy_recommendations_v1 (
                    recommendation_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL, UNIQUE(owner_id, fingerprint)
                );
                CREATE TABLE IF NOT EXISTS user_decisions_v1 (
                    decision_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    recommendation_id TEXT NOT NULL, payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(recommendation_id) REFERENCES policy_recommendations_v1
                );
            """)

    def save(self, owner_id: str, item: PolicyRecommendation) -> str:
        item.validate()
        payload = item.to_dict()
        fingerprint = _digest(payload)
        with self._connect() as connection:
            try:
                connection.execute("INSERT INTO policy_recommendations_v1 VALUES (?, ?, ?, ?, datetime('now'))",
                    (item.recommendation_id, owner_id, json.dumps(payload, ensure_ascii=False), fingerprint))
            except sqlite3.IntegrityError:
                row = connection.execute("""SELECT fingerprint FROM policy_recommendations_v1
                    WHERE recommendation_id = ? AND owner_id = ?""",
                    (item.recommendation_id, owner_id)).fetchone()
                if row is None or row[0] != fingerprint:
                    raise ValueError("Recommendation identity conflict")
        return item.recommendation_id

    def get(self, owner_id: str, recommendation_id: str) -> PolicyRecommendation:
        with self._connect() as connection:
            row = connection.execute("""SELECT payload_json FROM policy_recommendations_v1
                WHERE owner_id = ? AND recommendation_id = ?""",
                (owner_id, recommendation_id)).fetchone()
        if row is None:
            raise KeyError("Unknown recommendation")
        value = json.loads(row[0])
        value["rationale"] = tuple(value["rationale"])
        value["evidence_refs"] = tuple(value["evidence_refs"])
        value["confidence"]["reasons"] = tuple(value["confidence"]["reasons"])
        value["confidence"] = ConfidenceBreakdown(**value["confidence"])
        return PolicyRecommendation(**value)

    def decide(self, owner_id: str, recommendation_id: str, *, action: str,
               parameters: Mapping[str, Any] | None = None, comment: str = "",
               parameter_bounds: Mapping[str, tuple[float, float]] | None = None) -> UserDecision:
        recommendation = self.get(owner_id, recommendation_id)
        selected: dict[str, float] = {}
        if action == "accepted":
            selected = dict(recommendation.parameters)
        elif action == "modified":
            if not isinstance(parameters, Mapping) or set(parameters) != set(recommendation.parameters):
                raise ValueError("Modified decision must provide the complete parameter set")
            selected = {name: float(value) for name, value in parameters.items()}
        elif action != "rejected":
            raise ValueError("Decision must accept, modify or reject")
        if parameter_bounds:
            for name, value in selected.items():
                if name not in parameter_bounds or not parameter_bounds[name][0] <= value <= parameter_bounds[name][1]:
                    raise ValueError(f"Modified parameter is outside the study bounds: {name}")
        fingerprint = _digest(recommendation.to_dict())
        decision_seed = _digest({
            "recommendation": recommendation_id, "owner": owner_id,
            "action": action, "parameters": selected, "comment": comment,
        })[:24]
        decision_id = f"decision-{decision_seed}"
        decision = UserDecision(decision_id, recommendation_id, owner_id, action, selected,
                                str(comment)[:2000], fingerprint)
        decision.validate()
        with self._connect() as connection:
            connection.execute("INSERT OR IGNORE INTO user_decisions_v1 VALUES (?, ?, ?, ?, datetime('now'))",
                (decision.decision_id, owner_id, recommendation_id,
                 json.dumps(decision.to_dict(), ensure_ascii=False)))
        return decision

    def list(self, owner_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("""SELECT payload_json FROM policy_recommendations_v1
                WHERE owner_id = ? ORDER BY created_at DESC""", (owner_id,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def list_all(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("""SELECT payload_json FROM policy_recommendations_v1
                ORDER BY created_at DESC""").fetchall()
        return [json.loads(row[0]) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def automation_envelope(recommendation: PolicyRecommendation, *, exact_context: bool,
                        study_opt_in: bool, budget_available: bool) -> AutomationEnvelope:
    confidence = recommendation.confidence
    checks = {
        "exact_context": bool(exact_context),
        "minimum_samples": confidence.sample_count >= 20,
        "nearby_coverage": confidence.nearby_sample_count >= 5,
        "held_out_calibration": confidence.calibration == 1.0,
        "not_ood": not confidence.ood,
        "safety_constraints": confidence.safety == 1.0,
        "study_opt_in": bool(study_opt_in),
        "budget_available": bool(budget_available),
    }
    eligible = all(checks.values())
    envelope_seed = _digest({
        "recommendation": recommendation.recommendation_id, "checks": checks,
    })[:24]
    envelope = AutomationEnvelope(
        envelope_id=f"envelope-{envelope_seed}",
        recommendation_id=recommendation.recommendation_id, study_id=recommendation.study_id,
        eligible=eligible, status="eligible" if eligible else "not_eligible", checks=checks,
    )
    envelope.validate()
    return envelope
