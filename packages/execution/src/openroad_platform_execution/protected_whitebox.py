"""Protected white-box evaluation layered on the existing isolated patch gate."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from openroad_platform_contracts import EvidencePointer, MechanismEvidence

from .coding_agent import (
    CandidateEvaluation,
    IsolatedCodingAgent,
    PatchProposal,
    PromotionGate,
    VerificationPolicy,
)


@dataclass(frozen=True)
class WhiteBoxPolicy:
    verification: VerificationPolicy
    evidence_path: str
    mutation_root: str = "tools/OpenROAD/src/dpl_evolve/"
    forbidden_prefixes: tuple[str, ...] = (
        "baseline/", "scripts/", "flow/", "tools/OpenROAD/src/dpl/",
        "tools/OpenROAD/src/rsz/", "tools/OpenROAD/src/gpl/", "third_party/",
    )
    required_metrics: tuple[str, ...] = ("hpwl", "runtime_seconds")
    required_liveness_counters: tuple[str, ...] = ()

    def validate(self) -> None:
        self.verification.validate()
        path = Path(self.evidence_path)
        if path.is_absolute() or ".." in path.parts or not self.evidence_path:
            raise ValueError("White-box evidence_path must be candidate-relative")
        if not self.mutation_root.endswith("/") or self.mutation_root.startswith("/"):
            raise ValueError("White-box mutation_root must be a relative directory prefix")
        if any(not item or item.startswith("/") for item in self.forbidden_prefixes):
            raise ValueError("White-box forbidden prefixes must be relative")


@dataclass(frozen=True)
class ProtectedWhiteBoxEvaluation:
    evaluation: CandidateEvaluation
    status: str
    failure: str | None
    mechanism_evidence: MechanismEvidence | None
    evidence_file_sha256: str | None

    def to_dict(self) -> dict:
        return {
            "evaluation": self.evaluation.to_dict(), "status": self.status,
            "failure": self.failure,
            "mechanism_evidence": self.mechanism_evidence.to_dict()
            if self.mechanism_evidence else None,
            "evidence_file_sha256": self.evidence_file_sha256,
            "applied": False,
        }


class ProtectedWhiteBoxEvaluator:
    def __init__(self, coding_agent: IsolatedCodingAgent | None = None):
        self.coding_agent = coding_agent or IsolatedCodingAgent()

    def evaluate(self, repository: str | Path, proposal: PatchProposal,
                 candidate_worktree: str | Path,
                 policy: WhiteBoxPolicy) -> ProtectedWhiteBoxEvaluation:
        policy.validate()
        paths = proposal.validate()
        if any(path.startswith(policy.forbidden_prefixes) for path in paths):
            raise ValueError("White-box patch touches a protected evaluator or tool path")
        if any(not path.startswith(policy.mutation_root) for path in paths):
            raise ValueError("White-box patch escapes the declared mutation root")
        evaluation = self.coding_agent.evaluate(
            repository, proposal, candidate_worktree, policy.verification,
        )
        if evaluation.status != "passed":
            return ProtectedWhiteBoxEvaluation(
                evaluation, "failed", evaluation.failure, None, None,
            )
        candidate = Path(evaluation.candidate_worktree).resolve()
        evidence_path = (candidate / policy.evidence_path).resolve()
        try:
            evidence_path.relative_to(candidate)
        except ValueError as exc:
            raise ValueError("White-box evidence path escapes candidate workspace") from exc
        if not evidence_path.is_file() or evidence_path.stat().st_size > 2 * 1024 * 1024:
            return ProtectedWhiteBoxEvaluation(
                evaluation, "failed", "missing_or_oversized_mechanism_evidence", None, None,
            )
        raw = evidence_path.read_bytes()
        evidence_sha = hashlib.sha256(raw).hexdigest()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return ProtectedWhiteBoxEvaluation(
                evaluation, "failed", "malformed_mechanism_evidence", None, evidence_sha,
            )
        if not isinstance(payload, dict):
            return ProtectedWhiteBoxEvaluation(
                evaluation, "failed", "malformed_mechanism_evidence", None, evidence_sha,
            )
        baseline = payload.get("baseline_metrics") or {}
        candidate_metrics = payload.get("candidate_metrics") or {}
        liveness = payload.get("liveness") or {}
        missing = [name for name in policy.required_metrics
                   if name not in baseline or name not in candidate_metrics]
        if missing:
            return ProtectedWhiteBoxEvaluation(
                evaluation, "failed", "missing_required_metrics:" + ",".join(missing),
                None, evidence_sha,
            )
        counters = liveness.get("counters") or {}
        inactive = [name for name in policy.required_liveness_counters
                    if not isinstance(counters.get(name), (int, float))
                    or isinstance(counters.get(name), bool) or counters[name] <= 0]
        if liveness.get("status") != "live" or inactive:
            return ProtectedWhiteBoxEvaluation(
                evaluation, "failed", "mechanism_not_live"
                + ((":" + ",".join(inactive)) if inactive else ""), None, evidence_sha,
            )
        if payload.get("legality") is not True or payload.get("full_flow_validated") is not True:
            return ProtectedWhiteBoxEvaluation(
                evaluation, "failed", "legality_or_full_flow_gate_failed", None, evidence_sha,
            )
        mechanism = MechanismEvidence(
            evidence_id=f"mechanism-{evaluation.evaluation_id}",
            level=str(payload.get("level") or "target_local"),
            scope=str(payload.get("scope") or "other"),
            mechanism_intent=str(payload.get("mechanism_intent") or ""),
            source_reference={
                "kind": "patch", "ref": proposal.sha256,
                "files": list(evaluation.modified_files),
                "anchors": list(payload.get("source_anchors") or []),
            },
            controls=dict(payload.get("controls") or {}),
            liveness=dict(liveness),
            baseline_metrics={key: float(value) for key, value in baseline.items()},
            candidate_metrics={key: float(value) for key, value in candidate_metrics.items()},
            legality=True, full_flow_validated=True,
            review_outcome=str(payload.get("review_outcome") or "mechanism_evidence"),
            compatibility_observations=tuple(
                str(item) for item in payload.get("compatibility_observations", [])
            ),
            evidence=(
                EvidencePointer(ref=f"source:patch:{proposal.proposal_id}",
                                sha256=proposal.sha256),
                EvidencePointer(ref=f"artifact:whitebox:{evaluation.evaluation_id}",
                                sha256=evidence_sha),
            ),
        )
        try:
            mechanism.validate()
        except ValueError as exc:
            return ProtectedWhiteBoxEvaluation(
                evaluation, "failed", f"invalid_mechanism_evidence:{exc}", None, evidence_sha,
            )
        return ProtectedWhiteBoxEvaluation(
            evaluation, "passed", None, mechanism, evidence_sha,
        )

    def dispose(self, repository: str | Path,
                result: ProtectedWhiteBoxEvaluation) -> None:
        self.coding_agent.dispose(repository, result.evaluation)


class ProtectedWhiteBoxPromotionGate:
    """Review evidence and issue a receipt; never apply or merge a patch."""

    @staticmethod
    def review(result: ProtectedWhiteBoxEvaluation, policy: WhiteBoxPolicy, *,
               human_approved: bool = False) -> dict:
        if result.status != "passed" or result.mechanism_evidence is None:
            return {"decision": "rejected", "applied": False,
                    "evaluation_id": result.evaluation.evaluation_id,
                    "failure": result.failure}
        mechanism = result.mechanism_evidence
        if (mechanism.liveness.get("status") != "live" or not mechanism.legality
                or not mechanism.full_flow_validated):
            return {"decision": "rejected", "applied": False,
                    "evaluation_id": result.evaluation.evaluation_id,
                    "failure": "mechanism_gate_failed"}
        base = PromotionGate.review(
            result.evaluation, policy.verification, human_approved=human_approved,
        )
        return {**base, "mechanism_evidence_id": mechanism.evidence_id,
                "review_outcome": mechanism.review_outcome, "applied": False}
