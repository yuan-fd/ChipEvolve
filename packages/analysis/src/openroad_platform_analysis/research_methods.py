"""Traceable research-method registry for the platform learning plane.

The registry connects cited work to concrete, already testable implementation
symbols.  It does not turn a paper, prediction, or policy into an executor;
Workflow Runtime remains the only process authority.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass


@dataclass(frozen=True)
class ResearchMethod:
    method_id: str
    title: str
    publication: str
    doi: str
    arxiv: str | None
    role: str
    implementation: tuple[str, ...]
    platform_boundary: str
    maturity: str

    def validate(self) -> None:
        if not self.method_id or not self.title or not self.publication or not self.doi:
            raise ValueError("Research method citation is incomplete")
        if not self.implementation:
            raise ValueError("Research method has no implementation mapping")
        if self.maturity not in {"production-bounded", "offline-shadow", "evidence-only"}:
            raise ValueError("Invalid research method maturity")
        if "Runtime" not in self.platform_boundary:
            raise ValueError("Research method must preserve Runtime authority")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return dataclasses.asdict(self)


RESEARCH_METHODS = (
    ResearchMethod(
        "ptpt-mobo", "PTPT multi-objective Bayesian optimization", "IEEE TCAD 2022",
        "10.1109/TCAD.2022.3167858", None,
        "Pareto-aware multi-objective parameter planning",
        ("MultiObjectiveBayesianOptimizer", "pareto_front", "OptimizationStudyStore"),
        "Produces a bounded ExperimentPlan; only Workflow Runtime may execute it.",
        "production-bounded",
    ),
    ResearchMethod(
        "ord-qa-rag", "Customized RAG for EDA tool documentation QA", "ICCAD 2024",
        "10.1145/3676536.3676730", "2407.15353",
        "Version- and context-filtered evidence retrieval",
        ("EvidenceRAG", "PublicKnowledgeRegistry", "EvidencePointer"),
        "Returns cited evidence bundles with execution disabled; Workflow Runtime is unchanged.",
        "evidence-only",
    ),
    ResearchMethod(
        "drills-offline-rl", "DRiLLS deep reinforcement learning for logic synthesis",
        "ASP-DAC 2020", "10.1109/ASP-DAC47756.2020.9045559", None,
        "Reward trajectories and offline policy advice",
        ("build_trajectory", "BehaviorCloningShadowPolicy", "OfflineLinearQShadowPolicy"),
        "Policy output is shadow advice; user approval and Workflow Runtime are mandatory.",
        "offline-shadow",
    ),
    ResearchMethod(
        "analog-gp-surrogate", "Gaussian-process surrogate for analog circuit optimization",
        "Electronics 2020", "10.3390/electronics9040685", None,
        "Uncertainty-aware surrogate prediction and candidate ranking",
        ("GaussianProcessRegressorLite", "calibrate_gp", "assess_ood"),
        "Predictions remain non-canonical; only observed Workflow Runtime metrics may train the loop.",
        "production-bounded",
    ),
)


def research_method_catalog() -> dict[str, object]:
    for method in RESEARCH_METHODS:
        method.validate()
    return {
        "schema_version": 1,
        "methods": [method.to_dict() for method in RESEARCH_METHODS],
        "orchestration": [
            "EvidenceRAG retrieves cited context",
            "GP and RL-shadow estimate outcomes",
            "multi-objective BO emits an ExperimentPlan",
            "a human approves or modifies the recommendation",
            "Workflow Runtime executes and records observed evidence",
        ],
        "execution_authority": "Workflow Runtime",
    }
