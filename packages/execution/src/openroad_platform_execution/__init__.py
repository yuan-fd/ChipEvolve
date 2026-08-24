"""Execution-plane primitives for isolated EDA runs."""

from .adapter import AdapterExecution, ProcessAdapter
from .orfs_runner import ORFSRunner
from .orfs_plugin import (
    ORFS_PLUGIN_ID,
    ORFS_PLUGIN_VERSION,
    build_orfs_task,
    orfs_plugin_manifest,
)
from .process_guardian import ProcessGuardian, ProcessOutcome
from .registry import PluginRegistry
from .toolchain import ToolchainCatalog, ToolchainConfig, load_toolchain
from .rtlscout_plugin import (
    RTLSCOUT_PLUGIN_ID,
    RTLSCOUT_PLUGIN_VERSION,
    RTLSCOUT_UPSTREAM_COMMIT,
    build_rtlscout_task,
    build_rtlscout_spec_task,
    rtlscout_plugin_manifest,
)
from .rtl_verify_plugin import (
    RTL_VERIFY_PLUGIN_ID, RTL_VERIFY_PLUGIN_VERSION, build_rtl_verify_task,
    rtl_verify_plugin_manifest,
)
from .rtl_sim_plugin import (
    RTL_SIM_PLUGIN_ID, RTL_SIM_PLUGIN_VERSION, build_rtl_sim_task,
    rtl_sim_plugin_manifest,
)
from .rtl_mutation_plugin import (
    RTL_MUTATION_PLUGIN_ID, RTL_MUTATION_PLUGIN_VERSION, build_rtl_mutation_task,
    rtl_mutation_plugin_manifest,
)
from .rtl_formal_plugin import RTL_FORMAL_PLUGIN_ID, RTL_FORMAL_PLUGIN_VERSION, build_rtl_formal_task, rtl_formal_plugin_manifest
from .agenticpd_plugin import (
    AGENTICPD_PLUGIN_ID, AGENTICPD_PLUGIN_VERSION, AGENTICPD_UPSTREAM_COMMIT,
    agenticpd_plugin_manifest, build_agenticpd_task,
)
from .taiwei_plugin import (
    TAIWEI_3D_PLATFORMS, TAIWEI_OFFICIAL_CASES, TAIWEI_OPENROAD_COMMIT,
    TAIWEI_ORFS_COMMIT, TAIWEI_PLUGIN_ID,
    TAIWEI_PLUGIN_VERSION, TAIWEI_UPSTREAM_COMMIT, TaiWeiToolchainProfile,
    build_taiwei_task, taiwei_plugin_manifest, taiwei_technology_profiles,
)
from .implcraft_plugin import (
    IMPLCRAFT_PLUGIN_ID, IMPLCRAFT_PLUGIN_VERSION, IMPLCRAFT_UPSTREAM_COMMIT,
    build_implcraft_task, implcraft_plugin_manifest,
)
from .dplevolve_plugin import (
    DPLEVOLVE_LICENSE, DPLEVOLVE_PLUGIN_ID, DPLEVOLVE_PLUGIN_VERSION,
    DPLEVOLVE_UPSTREAM_COMMIT, build_dplevolve_audit_task,
    dplevolve_plugin_manifest, source_tree_digest,
)
from .coding_agent import (
    CandidateEvaluation, IsolatedCodingAgent, PatchProposal, PromotionGate,
    VerificationPolicy,
)
from .protected_whitebox import (
    ProtectedWhiteBoxEvaluation,
    ProtectedWhiteBoxEvaluator,
    ProtectedWhiteBoxPromotionGate,
    WhiteBoxPolicy,
)
from .craft_flow import (
    BackendNeutralFlowPlan, build_craft_flow_plan, craft_capability_matrix,
    craft_plan_to_task,
)
from .edacraft_extension import (
    EDACRAFT_COMPONENTS, EDACRAFT_PLUGIN_VERSION, EDACRAFT_UPSTREAM_COMMIT,
    EDACraftComponent, build_edacraft_task, edacraft_catalog,
    edacraft_component, edacraft_plugin_manifest,
)

__all__ = [
    "AdapterExecution", "ProcessAdapter", "ORFSRunner", "ProcessGuardian",
    "ProcessOutcome", "PluginRegistry", "ORFS_PLUGIN_ID", "ORFS_PLUGIN_VERSION",
    "build_orfs_task", "orfs_plugin_manifest", "ToolchainCatalog",
    "ToolchainConfig", "load_toolchain",
    "RTLSCOUT_PLUGIN_ID", "RTLSCOUT_PLUGIN_VERSION", "RTLSCOUT_UPSTREAM_COMMIT",
    "build_rtlscout_task", "build_rtlscout_spec_task", "rtlscout_plugin_manifest",
    "RTL_VERIFY_PLUGIN_ID", "RTL_VERIFY_PLUGIN_VERSION", "build_rtl_verify_task",
    "rtl_verify_plugin_manifest",
    "RTL_SIM_PLUGIN_ID", "RTL_SIM_PLUGIN_VERSION", "build_rtl_sim_task",
    "rtl_sim_plugin_manifest",
    "RTL_MUTATION_PLUGIN_ID", "RTL_MUTATION_PLUGIN_VERSION", "build_rtl_mutation_task",
    "rtl_mutation_plugin_manifest",
    "RTL_FORMAL_PLUGIN_ID", "RTL_FORMAL_PLUGIN_VERSION", "build_rtl_formal_task", "rtl_formal_plugin_manifest",
    "AGENTICPD_PLUGIN_ID", "AGENTICPD_PLUGIN_VERSION", "AGENTICPD_UPSTREAM_COMMIT",
    "agenticpd_plugin_manifest", "build_agenticpd_task",
    "TAIWEI_3D_PLATFORMS", "TAIWEI_OFFICIAL_CASES", "TAIWEI_OPENROAD_COMMIT",
    "TAIWEI_ORFS_COMMIT", "TAIWEI_PLUGIN_ID",
    "TAIWEI_PLUGIN_VERSION", "TAIWEI_UPSTREAM_COMMIT", "TaiWeiToolchainProfile",
    "build_taiwei_task", "taiwei_plugin_manifest", "taiwei_technology_profiles",
    "IMPLCRAFT_PLUGIN_ID", "IMPLCRAFT_PLUGIN_VERSION", "IMPLCRAFT_UPSTREAM_COMMIT",
    "build_implcraft_task", "implcraft_plugin_manifest",
    "DPLEVOLVE_LICENSE", "DPLEVOLVE_PLUGIN_ID", "DPLEVOLVE_PLUGIN_VERSION",
    "DPLEVOLVE_UPSTREAM_COMMIT", "build_dplevolve_audit_task",
    "dplevolve_plugin_manifest", "source_tree_digest",
    "CandidateEvaluation", "IsolatedCodingAgent", "PatchProposal", "PromotionGate",
    "VerificationPolicy",
    "ProtectedWhiteBoxEvaluation", "ProtectedWhiteBoxEvaluator",
    "ProtectedWhiteBoxPromotionGate", "WhiteBoxPolicy",
    "BackendNeutralFlowPlan", "build_craft_flow_plan", "craft_capability_matrix",
    "craft_plan_to_task",
    "EDACRAFT_COMPONENTS", "EDACRAFT_PLUGIN_VERSION", "EDACRAFT_UPSTREAM_COMMIT",
    "EDACraftComponent", "build_edacraft_task", "edacraft_catalog",
    "edacraft_component", "edacraft_plugin_manifest",
]
