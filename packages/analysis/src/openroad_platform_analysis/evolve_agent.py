"""Evidence-driven evolution proposals that are intentionally non-executable."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from .knowledge_base import EvidenceContext, EvidenceKnowledgeBase


@dataclass(frozen=True)
class EvolutionProposal:
    proposal_id: str
    objective: str
    context: EvidenceContext
    evidence: tuple[dict, ...]
    execution_allowed: bool = False
    required_gate: str = "coding_agent_isolated_validation"

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id, "objective": self.objective,
            "context": {
                "design_id": self.context.design_id, "platform": self.context.platform,
                "pdk_id": self.context.pdk_id, "toolchain_id": self.context.toolchain_id,
            },
            "evidence": list(self.evidence), "execution_allowed": False,
            "required_gate": self.required_gate,
        }


class EvidenceDrivenEvolveAgent:
    def __init__(self, knowledge: EvidenceKnowledgeBase):
        self.knowledge = knowledge

    def propose(self, objective: str, context: EvidenceContext) -> EvolutionProposal:
        objective = objective.strip()
        if not objective or len(objective) > 2000:
            raise ValueError("Evolution objective is empty or too long")
        hits = self.knowledge.search(objective, context, limit=5)
        if not hits:
            raise ValueError("No version-compatible evidence supports this evolution")
        evidence = tuple({
            "record_id": item["record_id"], "ref": item["evidence"]["ref"],
            "sha256": item["evidence"]["sha256"],
            "fingerprint": item["fingerprint"], "score": item["score"],
        } for item in hits)
        seed = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()[:12]
        return EvolutionProposal(f"evolve-{seed}-{uuid.uuid4().hex[:8]}", objective,
                                 context, evidence)
