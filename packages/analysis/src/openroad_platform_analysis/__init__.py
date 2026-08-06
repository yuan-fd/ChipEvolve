"""Deterministic ORFS evidence, diagnostics, layout density, and comparisons."""

from .diagnosis import diagnose
from .pipeline import analyze_run
from .reporter import build_llm_prompt, build_report

from .knowledge_base import (
    EvidenceContext, EvidenceKnowledgeBase, KnowledgeRecord,
)
from .evolve_agent import EvidenceDrivenEvolveAgent, EvolutionProposal
from .evidence_rag import EvidenceBundle, EvidenceKnowledgeRecordV2, EvidenceRAG
from .learning_data import LearningDatasetStore, RuntimeEvidenceExporter
from .optimization import (
    GaussianProcessRegressorLite,
    MultiObjectiveBayesianOptimizer,
    OptimizationStudyStore,
    pareto_front,
    proposal_to_experiment_plan,
)
from .offline_policy import (
    BehaviorCloningShadowPolicy,
    OfflineLinearQShadowPolicy,
    build_trajectory,
    split_by_design,
)

__all__ = [
    "analyze_run", "build_llm_prompt", "build_report", "diagnose",
    "EvidenceContext", "EvidenceKnowledgeBase", "KnowledgeRecord",
    "EvidenceDrivenEvolveAgent", "EvolutionProposal",
    "EvidenceBundle", "EvidenceKnowledgeRecordV2", "EvidenceRAG",
    "LearningDatasetStore", "RuntimeEvidenceExporter",
    "GaussianProcessRegressorLite", "MultiObjectiveBayesianOptimizer",
    "OptimizationStudyStore", "pareto_front", "proposal_to_experiment_plan",
    "BehaviorCloningShadowPolicy", "OfflineLinearQShadowPolicy",
    "build_trajectory", "split_by_design",
]
