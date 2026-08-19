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
from .iterative_agent import (
    AnalysisLayer, CoderAgent, DisruptorAgent, HeadroomLedger,
    HeadroomEntry, IterationLedger, IterationState, OptimizerAgent,
    OptimizerHypothesis, OptimizerPlan,
)
from .offline_policy import (
    BehaviorCloningShadowPolicy,
    OfflineLinearQShadowPolicy,
    build_trajectory,
    split_by_design,
)
from .open_knowledge import (
    BenchmarkDefinition, CorpusSnapshot, DocumentClaim, KnowledgeSource,
    PublicKnowledgeRegistry, load_public_manifest,
)
from .learning_collector import CollectionReceipt, LearningCollector, TenantLearningStore
from .recommendations import (
    AutomationEnvelope, ConfidenceBreakdown, PolicyRecommendation,
    RecommendationStore, UserDecision, automation_envelope, build_recommendation,
)
from .research_methods import RESEARCH_METHODS, ResearchMethod, research_method_catalog
from .calibration import (
    CalibrationReport, OODAssessment, assess_ood, bounded_benchmark_points, calibrate_gp,
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
    "BenchmarkDefinition", "CorpusSnapshot", "DocumentClaim", "KnowledgeSource",
    "PublicKnowledgeRegistry", "load_public_manifest",
    "CollectionReceipt", "LearningCollector", "TenantLearningStore",
    "AutomationEnvelope", "ConfidenceBreakdown", "PolicyRecommendation",
    "RecommendationStore", "UserDecision", "automation_envelope",
    "build_recommendation",
    "RESEARCH_METHODS", "ResearchMethod", "research_method_catalog",
    "CalibrationReport", "OODAssessment", "assess_ood",
    "bounded_benchmark_points", "calibrate_gp",
]
