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
from .lessons import LessonsStore, Lesson, distill_lesson, lesson_from_iteration
from .skills import SkillsStore, Skill, apply_skill
from .feedback_loop import FeedbackLoop, FeedbackOutcome
from .offline_policy import (
    BehaviorCloningShadowPolicy,
    OfflineLinearQShadowPolicy,
    OfflineInteractionQShadowPolicy,
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
from .design_ir import build_design_ir, design_ir_json, evidence_cards_from_design_ir
from .runtime_ir import build_run_evidence_ir, evidence_cards_from_run_ir
from .design_suite import list_design_packages, load_design_package
from .replication import compare_replication_reports, replication_report
from .causal_evidence import factorial_interaction_report, validate_holdout_interaction
from .causal_learning import followup_from_interaction, teacher_context_from_holdout
from .native_orfs_evidence import native_orfs_run_view
from .verification_evidence import generate_mutants, mutation_report, independent_verification_gate
from .edair import agent_evidence_view, artifact_ref, build_edair, physical_ir, timing_ir
from .hypothesis_ledger import HypothesisLedger, assess_hypothesis, promote_after_holdout, reflection_hypothesis
from .paper_harness import PaperProtocolStore, compare_arms, preregister_protocol, summarize_arm

__all__ = [
    "analyze_run", "build_llm_prompt", "build_report", "diagnose",
    "EvidenceContext", "EvidenceKnowledgeBase", "KnowledgeRecord",
    "EvidenceDrivenEvolveAgent", "EvolutionProposal",
    "EvidenceBundle", "EvidenceKnowledgeRecordV2", "EvidenceRAG",
    "LearningDatasetStore", "RuntimeEvidenceExporter",
    "GaussianProcessRegressorLite", "MultiObjectiveBayesianOptimizer",
    "OptimizationStudyStore", "pareto_front", "proposal_to_experiment_plan",
    "BehaviorCloningShadowPolicy", "OfflineLinearQShadowPolicy", "OfflineInteractionQShadowPolicy",
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
    "build_design_ir", "design_ir_json", "evidence_cards_from_design_ir",
    "build_run_evidence_ir", "evidence_cards_from_run_ir",
    "list_design_packages", "load_design_package",
    "replication_report", "compare_replication_reports",
    "factorial_interaction_report", "validate_holdout_interaction",
    "followup_from_interaction", "teacher_context_from_holdout", "native_orfs_run_view",
    "generate_mutants", "mutation_report", "independent_verification_gate",
    "artifact_ref", "timing_ir", "physical_ir", "build_edair", "agent_evidence_view",
    "HypothesisLedger", "reflection_hypothesis", "assess_hypothesis", "promote_after_holdout",
    "PaperProtocolStore", "preregister_protocol", "summarize_arm", "compare_arms",
]
