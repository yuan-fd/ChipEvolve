#!/usr/bin/env python3
"""Dependency-free local API and web entry point for OpenROAD Platform."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = ROOT / "apps" / "web"
PACKAGE_ROOTS = (
    ROOT / "packages" / "contracts" / "src",
    ROOT / "packages" / "execution" / "src",
    ROOT / "packages" / "scheduler" / "src",
    ROOT / "packages" / "analysis" / "src",
    ROOT / "packages" / "visualization" / "src",
)
for package_root in reversed(PACKAGE_ROOTS):
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))

from openroad_platform_contracts import (  # noqa: E402
    ActionSpec, EvidencePointer, ExperimentEdge, ExperimentNode, ExperimentNodeKind,
    LearningContext, PortSpec, RTLCandidate, RunRequest, RunStage, SpecIR, TaskSpec,
    VerificationPackage,
)
from openroad_platform_analysis import (  # noqa: E402
    EvidenceKnowledgeRecordV2, EvidenceRAG, GaussianProcessRegressorLite, RuntimeEvidenceExporter,
    LearningCollector, OptimizationStudyStore, PublicKnowledgeRegistry, RecommendationStore,
    TenantLearningStore, automation_envelope, build_recommendation,
    assess_ood, calibrate_gp, load_public_manifest, proposal_to_experiment_plan,
    build_run_evidence_ir, evidence_cards_from_run_ir, followup_from_interaction,
    teacher_context_from_holdout,
    replication_report, factorial_interaction_report, validate_holdout_interaction,
    agent_evidence_view, build_design_ir, build_edair, physical_ir,
    HypothesisLedger, assess_hypothesis, reflection_hypothesis, promote_after_holdout,
    PaperProtocolStore, preregister_protocol, summarize_arm, compare_arms,
)
from openroad_platform_execution import (  # noqa: E402
    PluginRegistry, ToolchainConfig, build_craft_flow_plan, build_orfs_task,
    build_rtlscout_task, build_rtlscout_spec_task,
    build_edacraft_task, craft_capability_matrix, craft_plan_to_task,
    edacraft_catalog, edacraft_component, edacraft_plugin_manifest,
    implcraft_plugin_manifest, orfs_plugin_manifest, rtlscout_plugin_manifest,
    TaiWeiToolchainProfile, TAIWEI_3D_PLATFORMS, build_taiwei_task, taiwei_plugin_manifest,
    taiwei_technology_profiles,
    build_rtl_verify_task, rtl_verify_plugin_manifest, build_rtl_sim_task,
    rtl_sim_plugin_manifest,
    build_rtl_mutation_task, rtl_mutation_plugin_manifest,
    build_rtl_formal_task, rtl_formal_plugin_manifest,
)
from openroad_platform_analysis.agent_trace import AgentTraceStore
from openroad_platform_analysis.iterative_agent import (
    AnalysisLayer, DisruptorAgent, IterationLedger, OptimizerAgent,
)  # noqa: E402
from openroad_platform_scheduler import (  # noqa: E402
    ALLOWED_MODELS, CampaignStore, CodexCliSpecProvider, JobStore,
    InMemorySecretBroker, NaturalLanguageTaskCompiler,
    OpenAICompatibleSpecProvider, ProviderProfile, ProviderProfileStore,
    RuleBasedSpecProvider, RuntimeStore,
    SpecConversationManager, SpecConversationStore, StageAwareCampaignManager,
    WorkflowRuntime, OptimizationCampaignBridge, RTLFrontendStore, ExperimentGraphStore,
    FourGateController, PatchRegistry, SpecProposal, EvolutionCampaign, EvolutionCampaignController,
    EvolutionCampaignStore, objective_profile, profile_grid, profile_hard_constraints,
)
try:  # Supports both `python apps/api/app.py` and package imports in tests.
    from .services import AuthSession, AuthStore, DesignService, PlatformReadModel  # type: ignore[attr-defined]
except ImportError:
    from services import AuthSession, AuthStore, DesignService, PlatformReadModel  # type: ignore[no-redef]


MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 2 * MAX_BODY_BYTES + 64 * 1024
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9_.-]+$")


@lru_cache(maxsize=1)
def _pinned_taiwei_manifest():
    """Validate the immutable production profile once per API/worker process."""
    source = ROOT / ".external-src" / "taiwei-pin-3d"
    tool_root = ROOT / ".tools" / "taiwei-official-3d"
    profile = TaiWeiToolchainProfile(
        orfs_root=tool_root / "orfs-research",
        openroad_bin=tool_root / "openroad-build-gcc12" / "bin" / "openroad",
        yosys_bin=tool_root / "orfs-research" / "tools" / "install" / "yosys" / "bin" / "yosys",
        runtime_library_paths=tuple(path for path in (
            tool_root / "dependencies" / "lib",
            tool_root / "dependencies" / "lib64",
            Path("/opt/openEuler/gcc-toolset-12/root/usr/lib64"),
        ) if path.is_dir()),
    )
    return taiwei_plugin_manifest(source, profile)


def _rtlscout_benchmarks() -> list[str]:
    """Dynamically scan pinned RTLScout benchmark directories.

    ``*_spirehdl`` variants need the Spire HDL environment and are not opened
    this round, so they are excluded. Falls back to ``["simple_adder"]`` when
    no benchmark directory is available.
    """
    benchmark_root = ROOT / ".external-src" / "rtlscout" / "benchmarks"
    benchmarks = []
    if benchmark_root.is_dir():
        for path in sorted(benchmark_root.iterdir()):
            if (path.is_dir() and not path.name.endswith("_spirehdl")
                    and (path / "metadata.json").is_file()):
                benchmarks.append(path.name)
    return benchmarks or ["simple_adder"]


def _taiwei_guidance(
    *,
    reason: str,
    message: str,
    message_zh: str,
    design_id: str = "",
    module: str = "",
    baseline_run_id: str = "",
) -> dict[str, Any]:
    """Build the structured guidance response for the guided TaiWei 3D submit flow.

    The Web layer returns this instead of a bare error so the frontend can drive
    the TaiWei detail panel from a machine-readable state instead of showing an
    unexplained failure.
    """
    result: dict[str, Any] = {
        "status": "guidance_required",
        "reason": reason,
        "message": message,
        "message_zh": message_zh,
        "supported_scope": "registered-design + supported 3D platform",
        "design_id": design_id,
        "module": module,
        "baseline_run_id": baseline_run_id,
    }
    return result


class ApiState:
    """Small application layer shared by the HTTP handler and tests."""

    def __init__(
        self,
        db_path: Path,
        upload_root: Path,
        orfs_root: Path,
        *,
        design_root: Path | None = None,
        legacy_root: Path | None = None,
        yosys_bin: Path | None = None,
        runtime_db_path: Path | None = None,
        campaign_db_path: Path | None = None,
        spec_db_path: Path | None = None,
        optimization_db_path: Path | None = None,
        rtl_frontend_db_path: Path | None = None,
        auth_db_path: Path | None = None,
        byok_transport_secure: bool | None = None,
        load_taiwei_plugin: bool = True,
    ):
        self.db_path = db_path.expanduser().resolve()
        self.byok_transport_secure = (True if byok_transport_secure is None
                                      else bool(byok_transport_secure))
        self.upload_root = upload_root.expanduser().resolve()
        self.orfs_root = orfs_root.expanduser().resolve()
        self.store = JobStore(self.db_path)
        local_state = Path(os.environ.get(
            "OPENROAD_PLATFORM_LOCAL_STATE",
            f"/tmp/openroad-platform-{os.getuid()}",
        ))
        state_root = ((runtime_db_path.expanduser().resolve().parent
                       if runtime_db_path is not None else local_state))
        self.local_state_root = state_root
        self.runtime_store = RuntimeStore(runtime_db_path or local_state / "runtime.db")
        self.campaign_store = CampaignStore(campaign_db_path or local_state / "campaign.db")
        self.spec_store = SpecConversationStore(spec_db_path or state_root / "spec.db")
        self.rtl_frontend = RTLFrontendStore(
            rtl_frontend_db_path or state_root / "rtl-frontend.db"
        )
        self.verification_oracle_root = state_root / "verification-oracles"
        self.verification_oracle_root.mkdir(parents=True, exist_ok=True)
        self.rtl_candidate_root = state_root / "rtl-candidates"
        self.rtl_candidate_root.mkdir(parents=True, exist_ok=True)
        self.experiment_graph = ExperimentGraphStore(state_root / "experiment-graph.db")
        self.hypothesis_ledger = HypothesisLedger(state_root / "hypothesis-ledger.db")
        self.paper_protocols = PaperProtocolStore(str(state_root / "paper-protocols.db"))
        self.patch_registry = PatchRegistry(state_root / "patch-registry.db")
        # Evidence RAG stores are deliberately owner-partitioned.  A learning
        # record may carry design fingerprints and run-derived evidence, so a
        # shared SQLite index would turn retrieval into a tenant boundary risk.
        self.evidence_rag_root = state_root / "evidence-rag"
        self.evidence_rag_root.mkdir(parents=True, exist_ok=True)
        self.optimization_store = OptimizationStudyStore(
            optimization_db_path or state_root / "optimization.db"
        )
        self.knowledge_registry = PublicKnowledgeRegistry(state_root / "public-knowledge.db")
        self.knowledge_registry.import_manifest(load_public_manifest(
            ROOT / "knowledge" / "public-corpus.lock.json"
        ))
        self.tenant_learning_store = TenantLearningStore(state_root / "tenant-learning.db")
        self.learning_collector = LearningCollector(self.runtime_store,
                                                    self.tenant_learning_store)
        self.agent_traces = AgentTraceStore(state_root / "agent-traces.db")
        self.provider_profiles = ProviderProfileStore(state_root / "provider-profiles.db")
        self.auth = AuthStore(auth_db_path or state_root / "web-auth.db")
        self.secret_broker = InMemorySecretBroker(default_ttl_seconds=8 * 3600)
        self.recommendation_store = RecommendationStore(state_root / "recommendations.db")
        self._spec_provider_bindings: dict[str, dict[str, str]] = {}
        self.server_spec_model = os.environ.get(
            "OPENROAD_PLATFORM_SERVER_SPEC_MODEL", "gpt-5.6-sol"
        ).strip()
        if self.server_spec_model not in ALLOWED_MODELS:
            raise ValueError("OPENROAD_PLATFORM_SERVER_SPEC_MODEL is not allowlisted")
        self.server_spec_model_ready = shutil.which("codex") is not None
        self.server_spec_daily_limit = int(os.environ.get(
            "OPENROAD_PLATFORM_SERVER_SPEC_DAILY_LIMIT", "20"
        ))
        self._server_spec_lock = threading.Lock()
        self.designs = DesignService(
            design_root or ROOT / "var" / "designs",
            legacy_root=legacy_root or Path(os.environ.get("ICCAD_ROOT", ROOT.parent / "iccad")),
            yosys_bin=yosys_bin or ROOT.parent / "bin" / "yosys",
        )
        toolchain = ToolchainConfig.from_environment(
            name="web-default", orfs_root=self.orfs_root,
            openroad_bin=_find_tool("openroad", ROOT.parent / "bin" / "openroad")
            or ROOT.parent / "bin" / "openroad",
            yosys_bin=_find_tool("yosys", ROOT.parent / "bin" / "yosys")
            or ROOT.parent / "bin" / "yosys",
            klayout_bin=_find_tool("klayout", ROOT.parent / "bin" / "klayout")
            or ROOT.parent / "bin" / "klayout",
        )
        manifests = [orfs_plugin_manifest(toolchain)]
        self.rtl_verify_readiness = {"ready": False, "reason": "Verilator or Yosys unavailable"}
        verifier = _find_tool("verilator", ROOT.parent / "bin" / "verilator")
        if verifier and toolchain.yosys_bin.is_file():
            try:
                manifests.append(rtl_verify_plugin_manifest(
                    verilator_bin=verifier, yosys_bin=toolchain.yosys_bin
                ))
                self.rtl_verify_readiness = {"ready": True, "reason": "Pinned local RTL verification tools are available"}
            except (FileNotFoundError, ValueError) as exc:
                self.rtl_verify_readiness["reason"] = str(exc)
        self.rtl_sim_readiness = {"ready": False, "reason": "Icarus Verilog simulator unavailable"}
        iverilog, vvp = (_find_tool("iverilog", ROOT.parent / "bin" / "iverilog"),
                          _find_tool("vvp", ROOT.parent / "bin" / "vvp"))
        if iverilog and vvp:
            try:
                manifests.append(rtl_sim_plugin_manifest(iverilog_bin=iverilog, vvp_bin=vvp))
                self.rtl_sim_readiness = {"ready": True, "reason": "Pinned Icarus simulation tools are available"}
            except (FileNotFoundError, ValueError) as exc:
                self.rtl_sim_readiness["reason"] = str(exc)
        self.rtl_mutation_readiness = {"ready": False, "reason": "Icarus mutation test tools unavailable"}
        if iverilog and vvp:
            try:
                manifests.append(rtl_mutation_plugin_manifest(iverilog_bin=iverilog, vvp_bin=vvp))
                self.rtl_mutation_readiness = {"ready": True, "reason": "Pinned mutation test tools are available"}
            except (FileNotFoundError, ValueError) as exc:
                self.rtl_mutation_readiness["reason"] = str(exc)
        self.rtl_formal_readiness = {"ready": False, "reason": "Pinned Yosys formal backend unavailable"}
        if toolchain.yosys_bin.is_file():
            try:
                manifests.append(rtl_formal_plugin_manifest(yosys_bin=toolchain.yosys_bin))
                self.rtl_formal_readiness = {"ready": True, "reason": "Pinned Yosys SAT formal backend is available"}
            except (FileNotFoundError, ValueError) as exc:
                self.rtl_formal_readiness["reason"] = str(exc)
        self.rtlscout_readiness = {
            "ready": False,
            "reason": "Pinned RTLScout source or isolated toolchain is unavailable",
        }
        rtlscout_source = ROOT / ".external-src" / "rtlscout"
        rtlscout_python = ROOT / ".tools" / "venvs" / "rtlscout312" / "bin" / "python"
        rtlscout_verilator = ROOT / ".tools" / "verilator-5.040" / "bin" / "verilator"
        try:
            manifests.append(rtlscout_plugin_manifest(
                rtlscout_source, rtlscout_python,
                verilator_bin=rtlscout_verilator, yosys_bin=toolchain.yosys_bin,
            ))
            self.rtlscout_readiness = {
                "ready": True,
                "reason": "Pinned source and isolated RTLScout toolchain are available",
            }
        except (FileNotFoundError, ValueError) as exc:
            self.rtlscout_readiness["reason"] = str(exc)
        edacraft_source = ROOT / ".external-src" / "edacraft"
        if edacraft_source.is_dir():
            for slug in ("rtlcraft", "edacode", "tcadcraft", "momcraft", "cktcraft"):
                manifests.append(edacraft_plugin_manifest(
                    slug, edacraft_source, Path(sys.executable)
                ))
            implcraft_python = ROOT / ".tools" / "venvs" / "implcraft" / "bin" / "python"
            if implcraft_python.is_file():
                manifests.append(implcraft_plugin_manifest(
                    edacraft_source, implcraft_python
                ))
        self.no_auth = os.environ.get("OPENROAD_PLATFORM_NO_AUTH", "").strip().lower() in {
            "1", "true", "yes", "on"}
        self.taiwei_readiness = {"ready": False, "reason": "Pinned 3D toolchain unavailable"}
        if load_taiwei_plugin:
            try:
                manifests.append(_pinned_taiwei_manifest())
                self.taiwei_readiness = {
                    "ready": True,
                    "reason": "Pinned official 3D toolchain is available",
                }
            except (FileNotFoundError, ValueError) as exc:
                self.taiwei_readiness["reason"] = str(exc)
        else:
            self.taiwei_readiness["reason"] = "Pinned 3D plugin loads on demand in this worker"
        self.runtime = WorkflowRuntime(
            self.runtime_store, PluginRegistry(manifests),
            workspace_root=state_root / "runtime-workspaces",
            environment_resolver=self._runtime_environment,
        )
        self.evolution_campaigns = EvolutionCampaignController(
            EvolutionCampaignStore(state_root / "evolution-campaign.db"), self.runtime
        )
        self.four_gate = FourGateController(self.experiment_graph, self.runtime)
        self.stage_campaigns = StageAwareCampaignManager(self.campaign_store, self.runtime)
        self.optimization_bridge = OptimizationCampaignBridge(self.stage_campaigns)
        self.platform = PlatformReadModel(
            designs=self.designs,
            runtime_store=self.runtime_store,
            campaign_store=self.campaign_store,
            optimization_store=self.optimization_store,
            knowledge_registry=self.knowledge_registry,
            recommendation_store=self.recommendation_store,
            tenant_learning_store=self.tenant_learning_store,
            extension_catalog=edacraft_catalog(),
        )

    def _runtime_environment(self, run) -> dict[str, str]:
        """Resolve a short-lived BYOK handle only at subprocess launch.

        The opaque handle may be persisted for audit/retry; its secret remains
        in the in-memory broker and is never included in TaskSpec JSON, logs,
        artifacts, or adapter request files.
        """
        task = run.task_spec
        handle = task.resources.get("credential_handle")
        if task.plugin_id != "rtlscout" or not isinstance(handle, str): return {}
        owner = str(task.labels.get("owner_id") or "")
        session_id = str(task.labels.get("provider_session_id") or "")
        provider = str(task.parameters.get("provider") or "")
        credential_env = {"anthropic": "ANTHROPIC_API_KEY", "deepinfra": "DEEPINFRA_API_KEY", "openrouter": "OPENROUTER_API_KEY"}.get(provider)
        if not owner or not session_id or not credential_env:
            raise ValueError("RTLScout BYOK task has invalid credential binding")
        return {credential_env: self.secret_broker.resolve(handle, owner_id=owner, session_id=session_id)}

    def ensure_taiwei_plugin(self) -> None:
        """Load the expensive optional 3D manifest only when a worker needs it."""
        try:
            self.runtime.registry.resolve("taiwei-pin-3d")
            return
        except LookupError:
            pass
        manifest = _pinned_taiwei_manifest()
        self.runtime.registry.register(manifest)
        self.taiwei_readiness = {
            "ready": True,
            "reason": "Pinned official 3D toolchain is available",
        }

    def _anonymous_session(self):
        """Local no-auth mode: a fixed developer session so all internal users
        share one workspace without browser registration. Login is preserved
        and restored by unsetting OPENROAD_PLATFORM_NO_AUTH."""
        return AuthSession(
            user_id="local-user", username="local-user",
            legacy_access=True, developer=True, session_id="no-auth-local",
        )

    def health(self) -> dict[str, Any]:
        openroad = _find_tool("openroad", ROOT.parent / "bin" / "openroad")
        yosys = _find_tool("yosys", ROOT.parent / "bin" / "yosys")
        heartbeat_path = Path(os.environ.get(
            "OPENROAD_PLATFORM_RUNTIME_WORKER_HEARTBEAT",
            self.local_state_root / "runtime-worker.heartbeat.json",
        ))
        worker = _read_worker_heartbeat(heartbeat_path)
        payload = {
            "ok": True,
            "service": "openroad-platform",
            "database": str(self.db_path),
            "database_ready": self.db_path.is_file(),
            "orfs_root": str(self.orfs_root),
            "orfs_ready": self.orfs_root.is_dir(),
            "openroad": openroad,
            "yosys": yosys,
            "execution_ready": bool(openroad and yosys and self.orfs_root.is_dir()),
            "runtime_worker_ready": worker["ready"],
            "runtime_worker_status": worker["status"],
            "runtime_worker_active_run": worker.get("active_run"),
            "runtime_worker_last_seen": worker.get("updated_at"),
            "byok_input_enabled": self.byok_transport_secure,
            "server_spec_model_ready": self.server_spec_model_ready,
            "server_spec_model": self.server_spec_model,
            "taiwei_3d_ready": self.taiwei_readiness["ready"],
            "taiwei_3d_reason": self.taiwei_readiness["reason"],
            "rtl_verification_ready": self.rtl_verify_readiness["ready"],
            "rtl_verification_reason": self.rtl_verify_readiness["reason"],
            "rtl_simulation_ready": self.rtl_sim_readiness["ready"],
            "rtl_simulation_reason": self.rtl_sim_readiness["reason"],
            "rtl_formal_ready": self.rtl_formal_readiness["ready"],
            "rtl_formal_reason": self.rtl_formal_readiness["reason"],
        }
        payload.update(self.designs.readiness())
        return payload

    @staticmethod
    def taiwei_technology_matrix() -> dict[str, Any]:
        return {
            "profiles": taiwei_technology_profiles(),
            "scope": "Only the listed pinned official 3D profiles are eligible; arbitrary process platforms are not supported.",
        }

    @staticmethod
    def _task_owned(task: Any, owner_id: str | None,
                    *, include_legacy: bool = False) -> bool:
        if owner_id is None:
            return True
        labels = task.labels if hasattr(task, "labels") else task.get("labels", {})
        recorded = str((labels or {}).get("owner_id") or "")
        return recorded == owner_id or (include_legacy and not recorded)

    def _owned_design(self, design_id: str, owner_id: str | None,
                      *, include_legacy: bool = False,
                      include_source: bool = False) -> dict[str, Any]:
        return self.designs.get(
            design_id, include_source=include_source, owner_id=owner_id,
            include_legacy=include_legacy,
        )

    def _authorize_runtime(self, run_id: str, owner_id: str | None,
                           *, include_legacy: bool = False):
        run = self.runtime_store.get_run(run_id)
        if not self._task_owned(run.task_spec, owner_id, include_legacy=include_legacy):
            raise KeyError(f"Unknown run: {run_id}")
        return run

    @staticmethod
    def projects() -> dict[str, Any]:
        return {
            "projects": [
                {
                    "id": "circuit-studio",
                    "name": "Circuit Studio",
                    "description": "Natural-language specification, RTL, netlist, schematic, and validation.",
                    "route": "frontend",
                    "status": "available",
                },
                {
                    "id": "physical-flow",
                    "name": "RTL-to-GDS Flow",
                    "description": "Six-stage ORFS implementation, campaigns, evidence, and 2D layout analysis.",
                    "route": "backend",
                    "status": "available",
                },
                {
                    "id": "taiwei-3d",
                    "name": "TaiWei 3D",
                    "description": "Pinned two-tier gcd flow, HBT metrics, artifacts, and replay evidence.",
                    "route": "backend",
                    "status": "available",
                },
                {
                    "id": "self-evolution",
                    "name": "Evidence-driven Evolution",
                    "description": "Evidence RAG, BO/GP, Pareto analysis, and human-controlled RL advice.",
                    "route": "evolution",
                    "status": "available",
                },
                {
                    "id": "edacraft-extension-pack",
                    "name": "Device & Circuit Research",
                    "description": "TCAD device simulation, interconnect EM extraction, and SPICE-level circuit analysis.",
                    "route": "backend",
                    "status": "available",
                },
            ],
            "extension_contract": {
                "manifest": "project id, name, description, route, status",
                "runtime": "adapter submits durable requests and returns evidence",
            },
        }

    def submit_edacraft_smoke(self, slug: str, *, owner_id: str | None = None) -> dict[str, Any]:
        component = edacraft_component(slug)
        if slug == "implcraft":
            raise ValueError("ImplCraft requires a registered RTL design; use the preserved Craft plan API")
        task = build_edacraft_task(slug)
        if owner_id:
            task = dataclasses.replace(task, labels={**task.labels, "owner_id": owner_id})
        run = self.runtime.submit(task, capability=component.capability)
        return {
            "run": self.get_runtime_run(run.run_id, owner_id=owner_id),
            "component": component.to_dict(),
            "execution_started": False,
            "notice": "Submitted to Workflow Runtime; a worker owns execution.",
        }

    def submit_edacraft_run(self, slug: str, payload: dict[str, Any], *,
                            owner_id: str | None = None,
                            include_legacy: bool = False) -> dict[str, Any]:
        """Submit an explicit device, interconnect, or circuit analysis input."""
        if slug not in {"tcadcraft", "momcraft", "cktcraft"}:
            raise ValueError("Only TCADCraft, MoMCraft, and CktCraft are available here")
        design_id = str(payload.get("design_id") or "").strip()
        if design_id:
            self._owned_design(design_id, owner_id, include_legacy=include_legacy)
        else:
            design_id = "device-research"

        inputs: dict[str, object] = {"input_origin": "user-supplied-specialist-input"}
        parameters: dict[str, object] = {}
        if slug == "tcadcraft":
            parameters = {
                "length_nm": _bounded_number(payload, "length_nm", 10.0, 1.0, 10_000.0),
                "width_nm": _bounded_number(payload, "width_nm", 5.0, 1.0, 10_000.0),
                "height_nm": _bounded_number(payload, "height_nm", 3.0, 1.0, 10_000.0),
            }
        elif slug == "momcraft":
            parameters = {
                "length_mm": _bounded_number(payload, "length_mm", 2.0, .01, 100.0),
                "width_mm": _bounded_number(payload, "width_mm", .5, .001, 20.0),
                "height_mm": _bounded_number(payload, "height_mm", .3, .001, 20.0),
                "eps_eff": _bounded_number(payload, "eps_eff", 3.2, 1.0, 30.0),
                "mesh_segments": int(_bounded_number(
                    payload, "mesh_segments", 4, 2, 64, integer=True
                )),
                "frequency_ghz": _bounded_number(
                    payload, "frequency_ghz", 1.0, .001, 300.0
                ),
            }
        else:
            netlist = str(payload.get("spice_netlist") or "").strip()
            if not netlist:
                raise ValueError("spice_netlist is required")
            if len(netlist.encode("utf-8")) > 64 * 1024:
                raise ValueError("SPICE netlist exceeds 64 KiB")
            if re.search(r"(?im)^\s*\.(?:include|lib|control|shell)\b", netlist):
                raise ValueError("External includes and control commands are not allowed")
            if not re.search(r"(?im)^\s*\.end\s*$", netlist):
                raise ValueError("SPICE netlist must end with .end")
            inputs["spice_netlist"] = netlist

        component = edacraft_component(slug)
        task = build_edacraft_task(
            slug, design_id=design_id, inputs=inputs, parameters=parameters
        )
        task = dataclasses.replace(task, labels={
            **task.labels,
            "source": "web-specialist-input",
            "linked_design_id": design_id,
            **({"owner_id": owner_id} if owner_id else {}),
        })
        run = self.runtime.submit(task, capability=component.capability)
        return {
            "run": self.get_runtime_run(
                run.run_id, owner_id=owner_id, include_legacy=include_legacy
            ),
            "component": component.to_dict(),
            "execution_started": False,
            "notice": "The specialist task is saved; the Runtime worker owns execution.",
        }

    def submit_taiwei_design_run(self, payload: dict[str, Any], *, owner_id: str | None = None,
                                 include_legacy: bool = False) -> dict[str, Any]:
        if not self.taiwei_readiness["ready"]:
            raise ValueError(self.taiwei_readiness["reason"])
        design_id = str(payload.get("design_id") or "").strip()
        design = self._owned_design(design_id, owner_id, include_legacy=include_legacy)
        case = str(design.get("module") or "").strip()
        if not case:
            raise ValueError("Registered design has no top module name")
        tech = str(payload.get("tech") or "asap7_3D")
        if tech not in TAIWEI_3D_PLATFORMS:
            raise ValueError(
                "Unsupported TaiWei 3D platform %r; choose from %s"
                % (tech, ", ".join(TAIWEI_3D_PLATFORMS)))
        # Optional 2D baseline association: no longer a hard gate. When present
        # it must reference a succeeded 2D ORFS run of the same registered
        # design; when absent the 3D flow runs on its own (TaiWei runs its own
        # internal 2D partition stage).
        baseline_run_id = str(payload.get("baseline_run_id") or "").strip()
        baseline = (self._authorize_runtime(
            baseline_run_id, owner_id, include_legacy=include_legacy
        ) if baseline_run_id else None)
        if baseline is not None and (
                baseline.task_spec.design_id != design_id
                or baseline.task_spec.plugin_id != "orfs"
                or baseline.status.value != "succeeded"):
            return _taiwei_guidance(
                reason="baseline_invalid",
                message=(
                    "The referenced 2D baseline must be a succeeded ORFS run of "
                    "the same registered design; it is optional, so omit it to "
                    "run the 3D flow standalone."
                ),
                message_zh=(
                    "关联的 2D 基线必须是同一登记设计的成功 ORFS 运行；该基线为可选项，"
                    "不传即可独立运行 3D 流程。"
                ),
                design_id=design_id,
                module=case,
                baseline_run_id=baseline_run_id,
            )
        rtl_path = self.designs.rtl_path(
            design_id, owner_id=owner_id, include_legacy=include_legacy)
        rtl = {
            "path": str(rtl_path),
            "size_bytes": rtl_path.stat().st_size,
            "sha256": _sha256(rtl_path),
        }
        clock = _optional_string(payload.get("clock"))
        clock_period_ns = _number(payload, "clock_period_ns", 10.0)
        parameters = {
            key: payload[key] for key in (
                "core_utilization_pct", "num_cores", "cts_layer", "outer_iterations",
                "skip_2d_part", "pin3d_allow_net_flow", "pin3d_split_net_flow",
                "abc_area", "start_from",
            ) if key in payload
        }
        task = build_taiwei_task(
            project_id="openroad-platform", design_id=case, tech=tech,
            registered_design_id=design_id, rtl=rtl, clock=clock,
            clock_period_ns=clock_period_ns, parameters=parameters,
        )
        labels = dict(task.labels)
        labels.update({"source": "web-linked-extension",
                       **({"baseline_run_id": baseline_run_id} if baseline_run_id else {}),
                       **({"owner_id": owner_id} if owner_id else {})})
        task = dataclasses.replace(task, labels=labels)
        run = self.runtime.submit(task, capability="eda.3d.pin3d")
        trace = self.agent_traces.create(
            "TaiWei 3D 流程（设计 %s）" % case, "taiwei-3d")
        trace.add("goal", "目标",
                  detail="双层（Pin-3D）物理实现 · %s · %s" % (case, tech))
        trace.add("plan", "配置 3D 实现参数",
                  metrics={"clock_period_ns": clock_period_ns,
                           "parameters": parameters})
        trace.add("tool_call", "提交到 3D 工具链", tool="taiwei-pin3d",
                  detail="run %s · RTL sha256 %s" % (run.run_id, rtl["sha256"][:12]))
        trace.add("evaluate", "任务已入队",
                  metrics={"status": run.status.value,
                           "design": case})
        trace.status = "done"
        trace.result = {"run_id": run.run_id, "design_id": design_id,
                        "tech": tech}
        self.agent_traces.save(trace)
        return self.get_runtime_run(run.run_id, owner_id=owner_id,
                                    include_legacy=include_legacy)

    def rtlscout_status(self) -> dict[str, Any]:
        codex_available = shutil.which("codex") is not None
        return {
            **self.rtlscout_readiness,
            "entry": "SpecIR + frozen verification oracle",
            "benchmark_submission": "removed",
            "fixed_suite": ["gcd", "fifo", "uart_tx", "ibex_alu"],
            "cost_metrics": ["transistors", "yosys_cells", "yosys_wires"],
            "codex_cli": {
                "available": codex_available,
                "model": self.server_spec_model,
                "mode": "platform_managed_internal_test",
                "note": "The server-owned Codex login is the only web RTLScout provider in v2.0; browser API-key input is disabled.",
            },
        }

    def submit_rtlscout(self, payload: dict[str, Any], *, owner_id: str | None = None) -> dict[str, Any]:
        raise RuntimeError(
            "Benchmark-only RTLScout submission was removed in v2; "
            "freeze a SpecIR and testbench, then use /api/rtl/specs/{spec_id}/rtlscout"
        )

    def submit_rtlscout_spec(self, spec_id: str, payload: dict[str, Any], *,
                             owner_id: str | None = None,
                             include_legacy: bool = False) -> dict[str, Any]:
        """The sole v2 RTL entry: reviewed SpecIR plus a frozen testbench."""
        if not self.rtlscout_readiness["ready"]:
            raise ValueError(str(self.rtlscout_readiness["reason"]))
        lineage = self.rtl_frontend.lineage(spec_id)
        owner = self.auth.owner_of("rtl_spec", spec_id)
        if owner_id and owner not in {owner_id, None} and not include_legacy:
            raise KeyError(spec_id)
        spec = SpecIR.from_dict(lineage["spec"])
        testbench = str(payload.get("testbench_source") or "")
        if not testbench.strip() or len(testbench.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("RTLScout-v2 requires a non-empty frozen testbench_source")
        _validate_rtlscout_testbench(testbench, spec.top)
        origin = str(payload.get("oracle_origin") or "")
        reviewed_by = str(payload.get("oracle_reviewed_by") or "").strip()
        if origin not in {"user_authored", "project_existing", "reference_model", "approved_generated"} or not reviewed_by:
            raise ValueError("A user-facing oracle requires declared origin and non-empty reviewer approval")
        digest = _sha256_text(testbench)
        oracle_path = self.verification_oracle_root / f"{digest}.sv"
        if oracle_path.exists() and oracle_path.read_text(encoding="utf-8") != testbench:
            raise RuntimeError("verification oracle hash collision")
        if not oracle_path.exists():
            oracle_path.write_text(testbench, encoding="utf-8")
        # The source is content-addressed; approval is scoped to a SpecIR and
        # therefore gets its own immutable receipt.  A later submission of the
        # same bytes for a different spec must not overwrite who approved the
        # first use.
        approval_path = self.verification_oracle_root / f"{digest}.{spec_id}.approval.json"
        approval = {
            "sha256": digest, "origin": origin, "reviewed_by": reviewed_by,
            "spec_id": spec_id, "approved_at": datetime.now(timezone.utc).isoformat(),
        }
        if approval_path.exists():
            existing = json.loads(approval_path.read_text(encoding="utf-8"))
            if {key: existing.get(key) for key in ("sha256", "origin", "reviewed_by", "spec_id")} != {
                key: approval[key] for key in ("sha256", "origin", "reviewed_by", "spec_id")
            }:
                raise RuntimeError("verification-oracle approval receipt is immutable")
        else:
            approval_path.write_text(json.dumps(approval, sort_keys=True), encoding="utf-8")
        package = VerificationPackage(
            verification_id=f"verify-{uuid.uuid4().hex}", spec_id=spec_id,
            compile_checks=("verilator-lint", "yosys-check"),
            simulation_oracle_refs=(f"artifact:verification-oracle:{digest}",),
        )
        self.rtl_frontend.add_verification_package(package)
        requested_model = str(payload.get("model") or "")
        fixture_mode = self.rtlscout_readiness.get("reason") == "fixture"
        if any(payload.get(key) for key in ("profile_id", "secret_handle")):
            raise ValueError("User-supplied provider profiles are disabled in v2 internal mode")
        if requested_model and not requested_model.startswith("codex-cli:") and not fixture_mode:
            raise ValueError("v2 internal mode uses only the platform-managed codex-cli RTLScout provider")
        credential_handle = None
        session_id = ""
        model = requested_model
        if not model and shutil.which("codex"):
            # This is not an API-key fallback.  The adapter starts an isolated
            # local Codex CLI session and protects/re-hashes the frozen oracle
            # around every candidate evaluation.
            model = "codex-cli:gpt-5.6-terra"
        if not model:
            raise ValueError("The platform-managed Codex CLI is unavailable; fixture models are test-only")
        task = build_rtlscout_spec_task(
            project_id="openroad-platform", spec=spec, verification=package,
            testbench_source=testbench,
            model=model,
            max_steps=max(1, min(int(payload.get("max_steps", 8)), 100)),
            cost_metric=str(payload.get("cost_metric") or "transistors"),
            labels={**({"owner_id": owner_id} if owner_id else {}),
                    **({"provider_session_id": session_id} if credential_handle else {})},
            oracle_provenance={"origin": origin, "reviewed_by": reviewed_by},
            credential_handle=credential_handle,
        )
        run = self.runtime.submit(task, capability="agent.rtl.generate")
        return {"run": self.get_runtime_run(run.run_id, owner_id=owner_id,
                                              include_legacy=include_legacy),
                "spec_id": spec_id, "verification_id": package.verification_id,
                "testbench_sha256": digest, "execution_started": False,
                "authority": "RTLScout-v2 is the sole RTL candidate producer"}

    def list_runs(self, limit: int = 50, *, owner_id: str | None = None,
                  include_legacy: bool = False) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        return [self._serialize_job(job) for job in self.store.list(limit=limit)
                if owner_id is None
                or job.request.labels.get("owner_id") == owner_id
                or (include_legacy and not job.request.labels.get("owner_id"))]

    def get_run(self, run_id: str, *, owner_id: str | None = None,
                include_legacy: bool = False) -> dict[str, Any]:
        job = self.store.get(run_id)
        recorded = job.request.labels.get("owner_id")
        if owner_id is not None and recorded != owner_id and not (include_legacy and not recorded):
            raise KeyError(f"Unknown run: {run_id}")
        payload = self._serialize_job(job)
        payload["events"] = self.store.events(run_id)
        result = payload.get("result") or {}
        workdir_value = result.get("workdir")
        if workdir_value:
            workdir = Path(workdir_value).expanduser().resolve()
            try:
                workdir.relative_to((ROOT / "var" / "runs").resolve())
                report_path = workdir / "analysis" / "report.json"
                if report_path.is_file():
                    payload["analysis_report"] = json.loads(
                        report_path.read_text(encoding="utf-8")
                    )
            except (ValueError, OSError, json.JSONDecodeError):
                pass
        return payload

    def submit_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        source = payload.get("rtl_source")
        filename = str(payload.get("filename") or "design.v").strip()
        if not isinstance(source, str) or not source.strip():
            raise ValueError("rtl_source must contain Verilog source code")
        if len(source.encode("utf-8")) > MAX_BODY_BYTES:
            raise ValueError("RTL source exceeds the 2 MiB upload limit")
        if (not SAFE_FILENAME.fullmatch(filename) or
                Path(filename).suffix.lower() not in {".v", ".sv"}):
            raise ValueError("filename must be a simple .v or .sv filename")

        run_id = uuid.uuid4().hex
        rtl_path = self.upload_root / run_id / filename
        request = RunRequest(
            rtl_path=str(rtl_path),
            top=_optional_string(payload.get("top")),
            clock=_optional_string(payload.get("clock")),
            clock_period_ns=_number(payload, "clock_period_ns", 10.0),
            platform=str(payload.get("platform") or "nangate45"),
            target_stage=RunStage(str(payload.get("target_stage") or "finish")),
            core_utilization_pct=_number(payload, "core_utilization_pct", 10.0),
            place_density=_number(payload, "place_density", 0.45),
            stage_timeout_seconds=int(_number(payload, "stage_timeout_seconds", 3600)),
            run_id=run_id,
            labels={"source": "web", **({"owner_id": str(payload["owner_id"])}
                                            if payload.get("owner_id") else {})},
        )
        request.validate(require_rtl=False)
        rtl_path.parent.mkdir(parents=True, exist_ok=False)
        rtl_path.write_text(source, encoding="utf-8")
        return self._serialize_job(self.store.submit(request))

    def begin_four_gate_baseline(self, payload: dict[str, Any], *, owner_id: str | None = None,
                                 include_legacy: bool = False) -> dict[str, Any]:
        design_id = str(payload.get("design_id") or "")
        design = self._owned_design(design_id, owner_id, include_legacy=include_legacy)
        task = build_orfs_task(
            self.designs.rtl_path(design_id, owner_id=owner_id, include_legacy=include_legacy),
            project_id="openroad-platform", design_id=design_id, top=design["module"],
            clock=_optional_string(payload.get("clock")),
            platform_name=str(payload.get("platform") or "nangate45"),
            target_stage=str(payload.get("target_stage") or "finish"),
            clock_period_ns=float(payload.get("clock_period_ns") or 10),
            core_utilization_pct=float(payload.get("core_utilization_pct") or 10),
            place_density=float(payload.get("place_density") or .45),
            labels={"four_gate": "baseline", **({"owner_id": owner_id} if owner_id else {})},
        )
        experiment_id, run_id = self.four_gate.begin_baseline(task, producer=owner_id or "local-user")
        return {"experiment_id": experiment_id,
                "run": self.get_runtime_run(run_id, owner_id=owner_id, include_legacy=include_legacy),
                "execution_started": False}

    def observe_four_gate_run(self, experiment_id: str, run_id: str, *, owner_id: str | None = None,
                              include_legacy: bool = False) -> dict[str, Any]:
        self.get_four_gate_graph(experiment_id, owner_id=owner_id, include_legacy=include_legacy)
        self._authorize_runtime(run_id, owner_id, include_legacy=include_legacy)
        node_id = self.four_gate.observe_baseline(experiment_id, run_id)
        return {"experiment_id": experiment_id, "observation_node_id": node_id,
                "graph": self.experiment_graph.describe(experiment_id)}

    def get_four_gate_graph(self, experiment_id: str, *, owner_id: str | None = None,
                            include_legacy: bool = False) -> dict[str, Any]:
        graph = self.experiment_graph.describe(experiment_id)
        baseline = next((node for node in graph["nodes"] if node["kind"] == "baseline"), None)
        if baseline is None:
            raise KeyError(experiment_id)
        task = TaskSpec.from_dict(baseline["payload"]["task"])
        if not self._task_owned(task, owner_id, include_legacy=include_legacy):
            raise KeyError(experiment_id)
        return graph

    def propose_four_gate_action(self, experiment_id: str, payload: dict[str, Any], *,
                                 owner_id: str | None = None,
                                 include_legacy: bool = False) -> dict[str, Any]:
        self.get_four_gate_graph(experiment_id, owner_id=owner_id, include_legacy=include_legacy)
        observation = str(payload.get("observation_node_id") or "")
        proposal_id = self.four_gate.propose(
            experiment_id, observation, producer=str(payload.get("producer") or "user"),
            payload=dict(payload.get("proposal") or {}),
            evidence_refs=tuple(payload.get("evidence_refs") or ()),
        )
        return {"experiment_id": experiment_id, "proposal_node_id": proposal_id,
                "graph": self.experiment_graph.describe(experiment_id)}

    def review_four_gate_action(self, payload: dict[str, Any], *, owner_id: str | None = None,
                                include_legacy: bool = False) -> dict[str, Any]:
        action = ActionSpec.from_dict(payload["action"])
        graph = self.get_four_gate_graph(action.experiment_id, owner_id=owner_id,
                                         include_legacy=include_legacy)
        baseline = next((node for node in graph["nodes"] if node["kind"] == "baseline"), None)
        if baseline is None:
            raise ValueError("Experiment has no baseline")
        task = TaskSpec.from_dict(baseline["payload"]["task"])
        run_id, attempt_id = self.four_gate.review_and_submit(action, task)
        return {"experiment_id": action.experiment_id, "attempt_node_id": attempt_id,
                "run": self.get_runtime_run(run_id, owner_id=owner_id,
                                            include_legacy=include_legacy), "execution_started": False}

    def measure_four_gate_attempt(self, experiment_id: str, attempt_node_id: str, *,
                                  owner_id: str | None = None,
                                  include_legacy: bool = False) -> dict[str, Any]:
        self.get_four_gate_graph(experiment_id, owner_id=owner_id, include_legacy=include_legacy)
        measurement = self.four_gate.observe_attempt(experiment_id, attempt_node_id)
        return {"experiment_id": experiment_id, "measurement_node_id": measurement,
                "graph": self.experiment_graph.describe(experiment_id)}

    def decide_four_gate_measurement(self, experiment_id: str, measurement_node_id: str,
                                     payload: dict[str, Any], *, owner_id: str | None = None,
                                     include_legacy: bool = False) -> dict[str, Any]:
        self.get_four_gate_graph(experiment_id, owner_id=owner_id, include_legacy=include_legacy)
        decision_id, memory_id = self.four_gate.decide_and_record_memory(
            experiment_id, measurement_node_id,
            producer=str(payload.get("producer") or owner_id or "reviewer"),
            outcome=str(payload.get("outcome") or ""),
            rationale=str(payload.get("rationale") or ""),
            memory_kind=str(payload.get("memory_kind") or "episodic"),
            evidence_refs=tuple(str(item) for item in payload.get("evidence_refs") or ()),
        )
        learning = self._index_four_gate_memory(
            experiment_id, measurement_node_id, payload, owner_id=owner_id,
        )
        return {"experiment_id": experiment_id, "decision_node_id": decision_id,
                "memory_node_id": memory_id,
                "learning": learning,
                "graph": self.experiment_graph.describe(experiment_id)}

    def _evidence_rag_for_owner(self, owner_id: str | None) -> EvidenceRAG:
        """Return an owner-partitioned RAG without using the owner as a path."""
        partition = hashlib.sha256((owner_id or "legacy-local").encode("utf-8")).hexdigest()
        return EvidenceRAG(self.evidence_rag_root / f"{partition}.db")

    def _index_four_gate_memory(self, experiment_id: str, measurement_node_id: str,
                                payload: dict[str, Any], *, owner_id: str | None) -> dict[str, Any]:
        """Index a decision as non-executable evidence-bound learning.

        Runtime is the sole source for the verified record.  Human rationale is
        useful as a negative/hypothesis memory, but is never made proposal
        eligible merely because it was submitted through this endpoint.
        """
        graph = self.experiment_graph.describe(experiment_id)
        measurement = next((node for node in graph["nodes"]
                            if node["node_id"] == measurement_node_id), None)
        if measurement is None:
            return {"indexed": False, "reason": "measurement node disappeared"}
        run_id = str(measurement.get("payload", {}).get("run_id") or "")
        if not run_id:
            return {"indexed": False, "reason": "measurement has no Runtime run"}
        try:
            run = self.runtime_store.get_run(run_id)
            context = self._learning_context_for_run(run)
        except (KeyError, ValueError) as exc:
            # Graph history remains valid even when a legacy task has no
            # immutable RTL fingerprint required by LearningContext.
            return {"indexed": False, "run_id": run_id,
                    "reason": f"not indexable: {exc}"}
        runtime_view = self.runtime_store.describe_run(run_id)
        canonical = json.dumps(runtime_view, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"))
        evidence = EvidencePointer(ref=f"run:{run_id}", sha256=_sha256_text(canonical))
        outcome = str(payload.get("outcome") or "")
        rationale = str(payload.get("rationale") or "").strip()
        rag = self._evidence_rag_for_owner(owner_id)
        records: list[str] = []
        fact = EvidenceKnowledgeRecordV2(
            claim=(f"Runtime recorded terminal status '{run.status.value}' for run {run_id}; "
                   "the complete measurement is cited by its immutable run evidence."),
            knowledge_type="observed_fact", context=context, evidence=evidence,
            verified=True, scope="exact_design",
            tags=("four-gate", "runtime", "terminal", run.status.value),
        )
        try:
            records.append(rag.add(fact))
        except Exception as exc:  # Duplicate is benign; persistence failures are reported.
            if "UNIQUE constraint failed" not in str(exc):
                return {"indexed": False, "run_id": run_id, "reason": str(exc)}
        if rationale:
            decision_record = EvidenceKnowledgeRecordV2(
                claim=f"Four-gate decision '{outcome}': {rationale}",
                knowledge_type="hypothesis" if outcome == "promoted" else "failed_attempt",
                context=context, evidence=evidence, verified=False, scope="exact_design",
                tags=("four-gate", "decision", outcome),
            )
            try:
                records.append(rag.add(decision_record))
            except Exception as exc:
                if "UNIQUE constraint failed" not in str(exc):
                    return {"indexed": False, "run_id": run_id, "reason": str(exc),
                            "record_ids": records}
        return {"indexed": True, "run_id": run_id, "record_ids": records,
                "execution_allowed": False,
                "note": "Only Runtime-derived facts are verified; reviewer rationale is non-executable."}

    def retrieve_runtime_learning(self, run_id: str, query: str, *, owner_id: str | None = None,
                                  include_legacy: bool = False, limit: int = 8) -> dict[str, Any]:
        """Retrieve owner-scoped, exact-context learning for a Runtime run."""
        run = self._authorize_runtime(run_id, owner_id, include_legacy=include_legacy)
        context = self._learning_context_for_run(run)
        bundle = self._evidence_rag_for_owner(owner_id).retrieve(
            query, context, limit=limit, action_eligible_only=False,
        )
        return {"run_id": run_id, "context_fingerprint": context.fingerprint,
                "bundle": bundle.to_dict(), "execution_allowed": False}

    def start_evolution_campaign(self, payload: dict[str, Any], *, owner_id: str | None = None,
                                 include_legacy: bool = False) -> dict[str, Any]:
        """Start a bounded automatic parameter-only campaign from one ORFS baseline."""
        design_id = str(payload.get("design_id") or "")
        design = self._owned_design(design_id, owner_id, include_legacy=include_legacy)
        parameter = str(payload.get("parameter") or "core_utilization_pct")
        values = tuple(float(item) for item in payload.get("values") or ())
        baseline = build_orfs_task(
            self.designs.rtl_path(design_id, owner_id=owner_id, include_legacy=include_legacy),
            project_id="openroad-platform", design_id=design_id, top=design["module"],
            clock=_optional_string(payload.get("clock")),
            platform_name=str(payload.get("platform") or "nangate45"),
            target_stage=str(payload.get("target_stage") or "finish"),
            clock_period_ns=float(payload.get("clock_period_ns") or 10),
            core_utilization_pct=float(payload.get("core_utilization_pct") or 10),
            place_density=float(payload.get("place_density") or .45),
            labels={"owner_id": owner_id} if owner_id else {},
        )
        campaign = EvolutionCampaign(
            campaign_id=f"evolution-{uuid.uuid4().hex}", baseline_task=baseline,
            parameter=parameter, values=values, repetitions=int(payload.get("repetitions") or 3),
            max_rounds=int(payload.get("max_rounds") or len(values)),
            stall_window=int(payload.get("stall_window") or 2),
            objective_metric=str(payload.get("objective_metric") or "finish__design__core__area"),
            redirect_parameter=_optional_string(payload.get("redirect_parameter")),
            redirect_values=tuple(float(item) for item in payload.get("redirect_values") or ()),
        )
        result = self.evolution_campaigns.start(campaign)
        if owner_id:
            self.auth.bind_resource("evolution_campaign", campaign.campaign_id, owner_id)
        self._project_evolution_campaign(result, owner_id=owner_id)
        return result

    def advance_evolution_campaign(self, campaign_id: str, payload: dict[str, Any], *,
                                   owner_id: str | None = None,
                                   include_legacy: bool = False) -> dict[str, Any]:
        owner = self.auth.owner_of("evolution_campaign", campaign_id)
        if owner_id and owner not in {owner_id, None} and not include_legacy:
            raise KeyError(campaign_id)
        # ``execute`` is deliberately explicit: a scheduler/worker may advance
        # automatically, while an HTTP review can observe only.
        result = self.evolution_campaigns.advance(
            campaign_id, execute=payload.get("execute") is True,
        )
        self._project_evolution_campaign(result, owner_id=owner_id)
        return result

    def _project_evolution_campaign(self, result: dict[str, Any], *, owner_id: str | None) -> None:
        """Project an automatic parameter-only campaign into the audit graph/RAG.

        This does not make an agent executable: the controller has already
        submitted only the predeclared parameter action to Runtime.  The
        projection preserves every terminal replica, including failures and
        no-improvement results, so later retrieval cannot learn only from
        winners.
        """
        campaign = result["campaign"]; state = result["state"]
        experiment_id = campaign["campaign_id"]; task = campaign["baseline_task"]
        now = datetime.now(timezone.utc).isoformat()
        existing = {node["node_id"] for node in self.experiment_graph.describe(experiment_id)["nodes"]}

        def node(node_id: str, kind: ExperimentNodeKind, producer: str, payload: dict[str, Any], refs=()):
            if node_id not in existing:
                self.experiment_graph.append_node(ExperimentNode(node_id, experiment_id, kind, producer, payload, tuple(refs), now))
                existing.add(node_id)
        def edge(parent: str, child: str, relation: str):
            try:
                self.experiment_graph.append_edge(ExperimentEdge(experiment_id, parent, child, relation))
            except ValueError as exc:
                if "already exists" not in str(exc): raise

        design_id, baseline_id = f"{experiment_id}-design", f"{experiment_id}-baseline"
        node(design_id, ExperimentNodeKind.DESIGN_REVISION, "evolution-policy-v2", {"design_id": task["design_id"], "task": task})
        node(baseline_id, ExperimentNodeKind.BASELINE, "evolution-policy-v2", {"task": task, "repetitions": campaign["repetitions"]})
        edge(design_id, baseline_id, "defines_baseline")
        baseline_observations = []
        for run_id in state.get("baseline_run_ids", []):
            run = self.runtime_store.get_run(run_id)
            if run.status.value not in {"succeeded", "failed", "cancelled", "timed_out"}: continue
            observation_id = f"{experiment_id}-baseline-observation-{run_id}"
            node(observation_id, ExperimentNodeKind.OBSERVATION, "runtime", {"run_id": run_id, "status": run.status.value}, (f"run:{run_id}",))
            edge(baseline_id, observation_id, "observed"); baseline_observations.append(observation_id)
            self._index_campaign_terminal_run(run_id, experiment_id, "baseline", owner_id)

        for item in state.get("history", []):
            phase = str(item["phase"]); round_id = phase.replace("-", "_")
            if phase == "baseline": continue
            proposal_id, review_id = f"{experiment_id}-proposal-{round_id}", f"{experiment_id}-review-{round_id}"
            parent = (f"{experiment_id}-memory-round_{int(phase.split('-')[-1]) - 1}"
                      if int(phase.split("-")[-1]) > 1 else (baseline_observations[0] if baseline_observations else baseline_id))
            phase_parameter = (campaign.get("redirect_parameter") if phase.startswith("redirect-")
                               else campaign["parameter"])
            node(proposal_id, ExperimentNodeKind.PROPOSAL, "evolution-policy-v2", {"phase": phase, "parameter": phase_parameter, "policy": "declared-parameter-only"}, tuple())
            edge(parent, proposal_id, "supports")
            node(review_id, ExperimentNodeKind.REVIEW, "evolution-policy-v2", {"approved": True, "scope": "declared parameter only", "phase": phase})
            edge(proposal_id, review_id, "approved")
            measurement_ids=[]
            for run_id in item["run_ids"]:
                run = self.runtime_store.get_run(run_id)
                attempt_id, measurement_id = f"{experiment_id}-attempt-{run_id}", f"{experiment_id}-measurement-{run_id}"
                node(attempt_id, ExperimentNodeKind.ATTEMPT, "runtime", {"run_id": run_id, "phase": phase}, (f"run:{run_id}",))
                edge(review_id, attempt_id, "executes")
                if run.status.value in {"succeeded", "failed", "cancelled", "timed_out"}:
                    node(measurement_id, ExperimentNodeKind.MEASUREMENT, "runtime", {"run_id": run_id, "status": run.status.value}, (f"run:{run_id}",))
                    edge(attempt_id, measurement_id, "measured"); measurement_ids.append(measurement_id)
                    self._index_campaign_terminal_run(run_id, experiment_id, phase, owner_id)
            if measurement_ids:
                outcome = "promoted" if item.get("median") == state.get("best_value") and item.get("failure_rate") == 0 else "no_improvement"
                decision_id, memory_id = f"{experiment_id}-decision-{round_id}", f"{experiment_id}-memory-{round_id}"
                node(decision_id, ExperimentNodeKind.DECISION, "evolution-policy-v2", {"outcome": outcome, "phase": phase, "summary": item}, tuple(f"run:{x}" for x in item["run_ids"]))
                edge(measurement_ids[0], decision_id, "decides")
                node(memory_id, ExperimentNodeKind.MEMORY, "evolution-policy-v2", {"memory_kind": "statistical", "outcome": outcome, "phase": phase}, tuple(f"run:{x}" for x in item["run_ids"]))
                edge(decision_id, memory_id, "learns")
        # Project a newly submitted round immediately, before Runtime executes
        # it.  The same deterministic IDs are later completed above with
        # measurements and learning nodes after terminal evidence exists.
        if state.get("status") in {"round_running", "redirect_running"} and state.get("active_run_ids"):
            phase = (f"redirect-{state['round']}" if state.get("status") == "redirect_running"
                     else f"round-{state['round']}")
            round_id = phase.replace("-", "_")
            proposal_id, review_id = f"{experiment_id}-proposal-{round_id}", f"{experiment_id}-review-{round_id}"
            parent = (f"{experiment_id}-memory-round_{state['round'] - 1}"
                      if state["round"] > 1 else (baseline_observations[0] if baseline_observations else baseline_id))
            phase_parameter = (campaign.get("redirect_parameter") if phase.startswith("redirect-")
                               else campaign["parameter"])
            node(proposal_id, ExperimentNodeKind.PROPOSAL, "evolution-policy-v2", {"phase": phase, "parameter": phase_parameter, "policy": "declared-parameter-only"}, tuple())
            edge(parent, proposal_id, "supports")
            node(review_id, ExperimentNodeKind.REVIEW, "evolution-policy-v2", {"approved": True, "scope": "declared parameter only", "phase": phase})
            edge(proposal_id, review_id, "approved")
            for run_id in state["active_run_ids"]:
                attempt_id = f"{experiment_id}-attempt-{run_id}"
                node(attempt_id, ExperimentNodeKind.ATTEMPT, "runtime", {"run_id": run_id, "phase": phase}, (f"run:{run_id}",))
                edge(review_id, attempt_id, "executes")
        if state.get("status") == "diagnosis_required":
            diagnosis_id = f"{experiment_id}-diagnosis"
            # A diagnosis is an interpretation of the measured baseline,
            # not an executable successor of a memory record.
            parent = baseline_observations[0] if baseline_observations else baseline_id
            node(diagnosis_id, ExperimentNodeKind.DIAGNOSIS, "evolution-policy-v2", dict(state.get("redirect") or {}), tuple())
            if parent in existing: edge(parent, diagnosis_id, "redirects")

    def _index_campaign_terminal_run(self, run_id: str, experiment_id: str, phase: str, owner_id: str | None) -> None:
        """Store immutable Runtime evidence as non-executable campaign knowledge."""
        try:
            run = self.runtime_store.get_run(run_id); context = self._learning_context_for_run(run)
        except (KeyError, ValueError): return
        canonical = json.dumps(self.runtime_store.describe_run(run_id), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        evidence = EvidencePointer(ref=f"run:{run_id}", sha256=_sha256_text(canonical))
        kind = "observed_fact" if run.status.value == "succeeded" else "failed_attempt"
        record = EvidenceKnowledgeRecordV2(
            claim=f"Evolution campaign {experiment_id} {phase} replica ended {run.status.value}; use cited Runtime evidence and replication statistics before judging QoR.",
            knowledge_type=kind, context=context, evidence=evidence, verified=(kind == "observed_fact"),
            scope="exact_design", tags=("evolution", "replica", phase, run.status.value),
        )
        try: self._evidence_rag_for_owner(owner_id).add(record)
        except Exception as exc:
            if "UNIQUE constraint failed" not in str(exc): raise

    def replication_qor_report(self, payload: dict[str, Any], *, owner_id: str | None = None,
                               include_legacy: bool = False) -> dict[str, Any]:
        run_ids = [str(item) for item in payload.get("run_ids") or ()]
        if len(run_ids) < 2 or len(run_ids) > 10:
            raise ValueError("run_ids must contain 2 to 10 Runtime runs")
        views = [self.get_runtime_run(run_id, owner_id=owner_id, include_legacy=include_legacy)
                 for run_id in run_ids]
        return replication_report(views, str(payload.get("metric") or ""))

    def causal_qor_report(self, payload: dict[str, Any], *, owner_id: str | None = None,
                           include_legacy: bool = False) -> dict[str, Any]:
        run_ids=[str(item) for item in payload.get("run_ids") or ()]
        if not 8 <= len(run_ids) <= 64: raise ValueError("causal report requires 8 to 64 Runtime runs")
        views=[self.get_runtime_run(run_id,owner_id=owner_id,include_legacy=include_legacy) for run_id in run_ids]
        first = str(payload.get("first_parameter") or "")
        second = str(payload.get("second_parameter") or "")
        metric = str(payload.get("metric") or "")
        report = factorial_interaction_report(views, first=first, second=second, metric=metric)
        report["learning_followup"] = followup_from_interaction(
            report, first=first, second=second, metric=metric
        )
        return report

    def validate_causal_holdout(self, payload: dict[str, Any], *, owner_id: str | None = None,
                                include_legacy: bool = False) -> dict[str, Any]:
        """Compare two pre-registered repeated 2x2 studies without auto-action."""
        source_ids = [str(item) for item in payload.get("source_run_ids") or ()]
        holdout_ids = [str(item) for item in payload.get("holdout_run_ids") or ()]
        if not 8 <= len(source_ids) <= 64 or not 8 <= len(holdout_ids) <= 64:
            raise ValueError("source_run_ids and holdout_run_ids each require 8 to 64 Runtime runs")
        first, second, metric = (str(payload.get("first_parameter") or ""),
                                 str(payload.get("second_parameter") or ""), str(payload.get("metric") or ""))
        source = factorial_interaction_report(
            [self.get_runtime_run(run_id, owner_id=owner_id, include_legacy=include_legacy) for run_id in source_ids],
            first=first, second=second, metric=metric)
        holdout = factorial_interaction_report(
            [self.get_runtime_run(run_id, owner_id=owner_id, include_legacy=include_legacy) for run_id in holdout_ids],
            first=first, second=second, metric=metric)
        validation = validate_holdout_interaction(
            source, holdout, first=first, second=second, metric=metric
        )
        return {"source": source, "holdout": holdout, "validation": validation,
                "teacher_context": teacher_context_from_holdout(
                    source, holdout, validation, first=first, second=second, metric=metric
                )}

    def create_evolution_hypothesis(self, payload: dict[str, Any], *, owner_id: str | None = None,
                                    include_legacy: bool = False) -> dict[str, Any]:
        """Record an LLM/reflection claim as a non-executable, falsifiable hypothesis."""
        run_ids = [str(item) for item in payload.get("run_ids") or ()]
        if not run_ids or len(run_ids) > 16:
            raise ValueError("run_ids must contain 1 to 16 cited Runtime runs")
        evidence = []
        for run_id in run_ids:
            view = self.get_runtime_run(run_id, owner_id=owner_id, include_legacy=include_legacy)
            evidence.append({"ref": f"run:{run_id}", "sha256": _sha256_text(json.dumps(view, sort_keys=True))})
        record = reflection_hypothesis(claim=str(payload.get("claim") or ""),
            mechanism=str(payload.get("mechanism") or ""), context=dict(payload.get("context") or {}),
            evidence_refs=evidence, producer=str(payload.get("producer") or "diagnosis-agent"),
            proposed_intervention=dict(payload.get("proposed_intervention") or {}))
        event_id = self.hypothesis_ledger.append(record)
        return {"event_id": event_id, "hypothesis": record,
                "authority": "hypothesis only; it cannot create a Runtime task"}

    def assess_evolution_hypothesis(self, hypothesis_id: str, payload: dict[str, Any], *,
                                    owner_id: str | None = None, include_legacy: bool = False) -> dict[str, Any]:
        history = self.hypothesis_ledger.history(hypothesis_id)
        if not history:
            raise KeyError(hypothesis_id)
        source = history[0]["record"]
        report = self.causal_qor_report(payload, owner_id=owner_id, include_legacy=include_legacy)
        assessment = assess_hypothesis(source, intervention_report=report,
                                       expected_direction=str(payload.get("expected_direction") or "min"))
        event_id = self.hypothesis_ledger.append({**source, **assessment})
        return {"event_id": event_id, "assessment": assessment}

    def preregister_paper_protocol(self, payload: dict[str, Any]) -> dict[str, Any]:
        protocol = preregister_protocol(study_id=str(payload.get("study_id") or ""),
            question=str(payload.get("question") or ""), designs=payload.get("designs") or (),
            arms=dict(payload.get("arms") or {}), metrics=dict(payload.get("metrics") or {}),
            repetitions=int(payload.get("repetitions") or 0), budget=dict(payload.get("budget") or {}),
            stopping_rule=str(payload.get("stopping_rule") or ""))
        self.paper_protocols.add(protocol)
        return protocol

    def summarize_paper_arms(self, payload: dict[str, Any]) -> dict[str, Any]:
        protocol = self.paper_protocols.get(str(payload.get("protocol_sha256") or ""))
        baseline = summarize_arm(protocol, arm=str(payload.get("baseline", {}).get("arm") or ""),
            design=str(payload.get("design") or ""), metric=str(payload.get("metric") or ""),
            values=payload.get("baseline", {}).get("values") or (), terminal_statuses=payload.get("baseline", {}).get("statuses") or ())
        candidate = summarize_arm(protocol, arm=str(payload.get("candidate", {}).get("arm") or ""),
            design=str(payload.get("design") or ""), metric=str(payload.get("metric") or ""),
            values=payload.get("candidate", {}).get("values") or (), terminal_statuses=payload.get("candidate", {}).get("statuses") or ())
        return {"baseline": baseline, "candidate": candidate,
                "comparison": compare_arms(baseline, candidate, minimum_relative_improvement=float(payload.get("minimum_relative_improvement", 0))) }

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        return self._serialize_job(self.store.request_cancel(run_id))

    def list_runtime_runs(self, limit: int = 50, *, owner_id: str | None = None,
                          include_legacy: bool = False,
                          design_id: str | None = None) -> dict[str, Any]:
        return {"runs": [{"run_id": run.run_id, "task_id": run.task_id,
                           "status": run.status.value, "created_at": run.created_at,
                           "started_at": run.started_at, "ended_at": run.ended_at,
                           "plugin_id": run.task_spec.plugin_id,
                           "project_id": run.task_spec.project_id,
                           "design_id": run.task_spec.design_id}
                          for run in self.runtime_store.list_runs(limit=limit)
                          if self._task_owned(run.task_spec, owner_id,
                                              include_legacy=include_legacy)
                          and (not design_id or run.task_spec.design_id == design_id)]}

    def get_runtime_run(self, run_id: str, *, owner_id: str | None = None,
                        include_legacy: bool = False) -> dict[str, Any]:
        self._authorize_runtime(run_id, owner_id, include_legacy=include_legacy)
        payload = self.runtime_store.describe_run(run_id)
        for stage in payload.get("stages", []):
            for attempt in stage.get("attempts", []):
                for artifact in attempt.get("artifacts", []):
                    artifact["url"] = (
                        f"/api/runtime/runs/{run_id}/artifacts/{artifact['artifact_id']}"
                    )
                    artifact["presentation"] = _artifact_presentation(artifact)
        analysis_report = self._runtime_analysis_report(payload)
        if analysis_report is not None:
            payload["analysis_report"] = analysis_report
        task = payload.get("run", {}).get("task_spec", {})
        if task.get("plugin_id") == "taiwei-pin-3d":
            payload["three_d"] = self._three_d_view(payload)
        payload["wait"] = self._wait_summary(run_id)
        return payload

    def runtime_evidence_ir(self, run_id: str, *, owner_id: str | None = None,
                            include_legacy: bool = False) -> dict[str, Any]:
        view = self.get_runtime_run(run_id, owner_id=owner_id, include_legacy=include_legacy)
        ir = build_run_evidence_ir(view)
        return {"run_evidence_ir": ir, "evidence_cards": evidence_cards_from_run_ir(ir),
                "authority": "RuntimeStore projection; cards are factual and non-executable"}

    def runtime_edair(self, run_id: str, *, owner_id: str | None = None,
                      include_legacy: bool = False) -> dict[str, Any]:
        """Expose provenance-first EDAIR without discarding raw Runtime artifacts."""
        view = self.get_runtime_run(run_id, owner_id=owner_id, include_legacy=include_legacy)
        run_ir = build_run_evidence_ir(view)
        artifacts = [item for stage in view.get("stages", []) for attempt in stage.get("attempts", [])
                     for item in attempt.get("artifacts", []) if item.get("artifact_id") and item.get("sha256")]
        refs = [{"artifact_id": item["artifact_id"], "sha256": item["sha256"], "kind": item.get("kind", "artifact"),
                 "parser": "runtime-registry", "parser_version": "v1", "source_size_bytes": item.get("size_bytes")}
                for item in artifacts]
        # Project only artifacts whose registered bytes still verify.  This is
        # deliberately best-effort: a missing Netlist/DEF parser must remain
        # ``None``, never become an LLM-invented physical fact.
        design_ir = None
        netlist = next((item for item in artifacts if item.get("kind") == "netlist"), None)
        if netlist:
            attempt = next((a for s in view.get("stages", []) for a in s.get("attempts", [])
                            if any(x.get("artifact_id") == netlist["artifact_id"] for x in a.get("artifacts", []))), None)
            path = Path(attempt["workspace"]) / netlist["store_key"] if attempt else None
            if path and path.is_file() and _sha256(path) == netlist["sha256"]:
                design_ir = build_design_ir(path)
        physical = None
        envelope = view.get("analysis_report") or {}
        report = envelope.get("report") if isinstance(envelope, dict) else None
        if isinstance(report, dict) and isinstance(envelope.get("source_sha256"), str):
            diagnosis = report.get("diagnosis") if isinstance(report.get("diagnosis"), dict) else {}
            rows = diagnosis.get("violations") if isinstance(diagnosis.get("violations"), list) else []
            source = {"artifact_id": str(envelope.get("source_artifact_id") or "analysis-report"),
                      "sha256": envelope["source_sha256"], "kind": "report",
                      "parser": "openroad-analysis-report", "parser_version": "v1",
                      "source_size_bytes": envelope.get("source_size_bytes")}
            physical = physical_ir(instances=(), nets=(), violations=[
                {"rule": str(row.get("type") or "reported_violation"), "severity": row.get("severity")}
                for row in rows if isinstance(row, dict)], source=source,
                grid={"available": bool((report.get("cell_density") or {}).get("available"))},
                truncated=len(rows) > 256)
        edair = build_edair(design=design_ir, run=run_ir, physical=physical, raw_artifacts=refs)
        return {"edair": edair, "agent_view": agent_evidence_view(edair),
                "authority": "raw Runtime artifacts remain authoritative; EDAIR is a versioned projection"}

    def _wait_summary(self, run_id: str) -> dict[str, Any]:
        target = self.runtime_store.get_run(run_id)
        waiting = [run for run in reversed(self.runtime_store.list_runs(limit=500))
                   if run.status.value in {"queued", "preparing", "retry_wait"}]
        active = [run for run in self.runtime_store.list_runs(limit=500)
                  if run.run_id != run_id
                  and run.status.value in {"running", "cancel_requested"}]
        predecessors = list(active)
        if target.status.value in {"queued", "preparing", "retry_wait"}:
            for run in waiting:
                if run.run_id == run_id:
                    break
                predecessors.append(run)
        estimates = {
            "orfs": 120, "rtlscout": 45, "taiwei-pin-3d": 21_600,
            "edacraft-tcadcraft": 120, "edacraft-momcraft": 120,
            "edacraft-cktcraft": 120, "edacraft-edacode": 60,
        }
        estimated = 0
        if target.status.value in {"queued", "preparing", "retry_wait"}:
            estimated = sum(estimates.get(str(run.task_spec.plugin_id), 120)
                            for run in predecessors)
        people = {str(run.task_spec.labels.get("owner_id") or f"legacy-{run.run_id}")
                  for run in predecessors}
        return {
            "people_ahead": len(people),
            "tasks_ahead": len(predecessors),
            "estimated_wait_seconds": estimated,
            "status": target.status.value,
        }

    @staticmethod
    def _runtime_analysis_report(payload: dict[str, Any]) -> dict[str, Any] | None:
        for stage in reversed(payload.get("stages", [])):
            for attempt in reversed(stage.get("attempts", [])):
                workspace = Path(attempt["workspace"]).expanduser().resolve()
                for artifact in attempt.get("artifacts", []):
                    if not (artifact.get("kind") == "report"
                            and str(artifact.get("store_key", "")).endswith(
                                "analysis/report.json")):
                        continue
                    path = (workspace / artifact["store_key"]).resolve()
                    try:
                        path.relative_to(workspace)
                    except ValueError:
                        continue
                    if (not path.is_file() or path.stat().st_size != artifact["size_bytes"]
                            or _sha256(path) != artifact["sha256"]):
                        continue
                    try:
                        report = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, ValueError, json.JSONDecodeError):
                        continue
                    if not isinstance(report, dict):
                        continue
                    report.pop("llm_prompt", None)
                    return {
                        "source_artifact_id": artifact["artifact_id"],
                        "source_sha256": artifact["sha256"],
                        "source_size_bytes": artifact["size_bytes"],
                        "source_url": artifact.get("url"),
                        "report": report,
                    }
        return None

    def runtime_artifact(self, run_id: str, artifact_id: str, *,
                         owner_id: str | None = None,
                         include_legacy: bool = False) -> tuple[Path, str]:
        self._authorize_runtime(run_id, owner_id, include_legacy=include_legacy)
        payload = self.runtime_store.describe_run(run_id)
        for stage in payload.get("stages", []):
            for attempt in stage.get("attempts", []):
                workspace = Path(attempt["workspace"]).expanduser().resolve()
                for artifact in attempt.get("artifacts", []):
                    if artifact["artifact_id"] != artifact_id:
                        continue
                    path = (workspace / artifact["store_key"]).resolve()
                    try:
                        path.relative_to(workspace)
                    except ValueError as exc:
                        raise ValueError("artifact path escapes its Runtime workspace") from exc
                    if not path.is_file():
                        raise KeyError(f"Artifact file is missing: {artifact_id}")
                    if (path.stat().st_size != artifact["size_bytes"]
                            or _sha256(path) != artifact["sha256"]):
                        raise ValueError(f"Artifact integrity check failed: {artifact_id}")
                    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                    return path, content_type
        raise KeyError(f"Unknown artifact for run: {artifact_id}")

    def _three_d_view(self, payload: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "tiers": {}, "metrics": {}, "toolchain": {}, "artifacts": [],
            "replayable": False,
        }
        for stage in payload.get("stages", []):
            for attempt in stage.get("attempts", []):
                workspace = Path(attempt["workspace"]).expanduser().resolve()
                for artifact in attempt.get("artifacts", []):
                    item = {key: artifact.get(key) for key in (
                        "artifact_id", "kind", "store_key", "size_bytes", "sha256", "url"
                    )}
                    path = (workspace / artifact["store_key"]).resolve()
                    try:
                        path.relative_to(workspace)
                    except ValueError:
                        item["integrity_verified"] = False
                        result["artifacts"].append(item)
                        continue
                    item["integrity_verified"] = (
                        path.is_file()
                        and path.stat().st_size == artifact["size_bytes"]
                        and _sha256(path) == artifact["sha256"]
                    )
                    result["artifacts"].append(item)
                    if not item["integrity_verified"]:
                        continue
                    if artifact["kind"] not in {
                        "three_d_eval", "toolchain_snapshot", "three_d_report"
                    }:
                        continue
                    if not path.is_file() or path.suffix != ".json":
                        continue
                    try:
                        value = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        continue
                    if artifact["kind"] == "three_d_eval":
                        result["metrics"] = value
                    elif artifact["kind"] == "toolchain_snapshot":
                        result["toolchain"] = value
                    elif path.name == "tier_view_metrics.json":
                        result["tiers"] = value
        required = {"gds", "def", "odb", "netlist", "three_d_eval", "toolchain_snapshot"}
        result["replayable"] = required.issubset(
            {item["kind"] for item in result["artifacts"]
             if item["integrity_verified"]}
        )
        return result

    def cancel_runtime_run(self, run_id: str, *, owner_id: str | None = None,
                           include_legacy: bool = False) -> dict[str, Any]:
        self._authorize_runtime(run_id, owner_id, include_legacy=include_legacy)
        self.runtime_store.request_cancel(run_id)
        return self.get_runtime_run(run_id, owner_id=owner_id,
                                    include_legacy=include_legacy)

    def list_campaigns(self, *, owner_id: str | None = None,
                       include_legacy: bool = False) -> dict[str, Any]:
        campaigns = []
        for item in self.campaign_store.list():
            try:
                campaigns.append(self.get_campaign(
                    item["campaign_id"], owner_id=owner_id,
                    include_legacy=include_legacy,
                ))
            except KeyError:
                continue
        return {"campaigns": campaigns}

    def get_campaign(self, campaign_id: str, *, owner_id: str | None = None,
                     include_legacy: bool = False) -> dict[str, Any]:
        if owner_id and not self.auth.owns_resource(
            "campaign", campaign_id, owner_id, include_legacy=include_legacy
        ):
            raise KeyError(f"Unknown campaign: {campaign_id}")
        try:
            result = self.stage_campaigns.describe(campaign_id)
            members = {
                item.member_id: item for item in self.campaign_store.members(campaign_id)
            }
            result["members"] = [
                {
                    **item,
                    "design_id": members[item["member_id"]].task_spec.design_id,
                    "parameters": dict(
                        members[item["member_id"]].task_spec.parameters
                    ),
                }
                for item in result["members"]
            ]
            return result
        except KeyError:
            pass
        campaign = self.campaign_store.get(campaign_id)
        members = []
        for member in self.campaign_store.members(campaign_id):
            run = self.runtime_store.get_run(member.run_id) if member.run_id else None
            members.append({"member_id": member.member_id, "ordinal": member.ordinal,
                            "task_id": member.task_spec.task_id, "run_id": member.run_id,
                            "status": run.status.value if run else "unbound"})
        return {**campaign, "members": members}

    def list_optimization_studies(self, *, owner_id: str | None = None,
                                  include_legacy: bool = False) -> dict[str, Any]:
        allowed = {item["id"] for item in self.designs.list(
            limit=100, owner_id=owner_id, include_legacy=include_legacy
        )} if owner_id else None
        return {"studies": [item for item in self.optimization_store.list()
                             if allowed is None or item.get("design_id") in allowed]}

    def get_optimization_study(self, study_id: str, *, owner_id: str | None = None,
                               include_legacy: bool = False) -> dict[str, Any]:
        detail = self.optimization_store.describe(study_id)
        if owner_id:
            self._owned_design(
                detail["study"]["design_id"], owner_id, include_legacy=include_legacy
            )
        return detail

    def public_knowledge(self, query: dict[str, list[str]] | None = None) -> dict[str, Any]:
        query = query or {}
        text = (query.get("q") or [""])[0]
        results = []
        if text:
            results = self.knowledge_registry.search(
                text, platform=(query.get("platform") or ["nangate45"])[0],
                toolchain=(query.get("toolchain") or [""])[0],
                stage=(query.get("stage") or ["finish"])[0],
                design_class=(query.get("design_class") or ["digital"])[0],
            )
        return {"sources": self.knowledge_registry.list_sources(),
                "benchmarks": self.knowledge_registry.list_benchmarks(),
                "results": results, "knowledge_origin": "external_public",
                "local_observation": False}

    def save_provider_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        # v2.0 is an internal evaluation deployment.  Accepting a browser
        # supplied provider credential here would create a second, unreviewed
        # authority path beside the platform-managed Codex service.
        raise ValueError(
            "User-supplied provider credentials are disabled in v2 internal mode; "
            "the platform-managed Codex service is used instead"
        )

    def _save_provider_profile_legacy(self, payload: dict[str, Any]) -> dict[str, Any]:
        owner_id = str(payload.get("owner_id") or "local-user")
        session_id = str(payload.get("session_id") or "local-session")
        api_key = payload.get("api_key")
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("api_key is required and is held in memory only")
        profile = ProviderProfile(
            profile_id=str(payload.get("profile_id") or f"provider-{uuid.uuid4().hex}"),
            owner_id=owner_id, provider_type="openai-compatible-byok",
            base_url=str(payload.get("base_url") or ""), model=str(payload.get("model") or ""),
            timeout_seconds=int(payload.get("timeout_seconds", 60)),
            max_response_bytes=int(payload.get("max_response_bytes", 1_048_576)),
            max_calls=int(payload.get("max_calls", 8)),
            allow_private_endpoint=payload.get("allow_private_endpoint") is True,
        )
        if not self._byok_transport_available():
            raise ValueError("BYOK key input is disabled until the external service uses HTTPS")
        provider_host = urlparse(profile.base_url).hostname or ""
        allowed_hosts = {
            "api.openai.com", "api.anthropic.com", "api.deepinfra.com", "openrouter.ai",
            "localhost", "127.0.0.1", "::1",
        }
        allowed_hosts.update(item.strip().lower() for item in os.environ.get(
            "OPENROAD_PLATFORM_PROVIDER_ALLOW_HOSTS", "").split(",") if item.strip())
        if provider_host.lower() not in allowed_hosts:
            raise ValueError("Provider host is not in the administrator egress allowlist")
        profile_id = self.provider_profiles.save(profile)
        handle = self.secret_broker.put(api_key, owner_id=owner_id, session_id=session_id)
        return {"profile_id": profile_id, "owner_id": owner_id, "session_id": session_id,
                "model": profile.model, "base_url": profile.base_url,
                "secret": self.secret_broker.describe(handle, owner_id=owner_id,
                                                        session_id=session_id),
                "persistence": "profile-only; API key is memory-only",
                "api_key": None}

    def list_provider_profiles(self, owner_id: str) -> dict[str, Any]:
        return {"profiles": self.provider_profiles.list(owner_id=owner_id),
                "secret_persistence": "memory-only", "default_ttl_seconds": 8 * 3600}

    def revoke_provider_secret(self, payload: dict[str, Any]) -> dict[str, Any]:
        revoked = self.secret_broker.revoke(
            str(payload.get("secret_handle") or ""),
            owner_id=str(payload.get("owner_id") or ""),
            session_id=str(payload.get("session_id") or ""),
        )
        return {"revoked": revoked}

    def _learning_context_for_run(self, run, *, metric_parser_version=None):
        """Build the learning context for a run (shared by manual + auto paths)."""
        rtl = run.task_spec.inputs.get("rtl")
        rtl_sha = rtl.get("sha256") if isinstance(rtl, dict) else run.task_spec.inputs.get("rtl_sha256")
        if not isinstance(rtl_sha, str):
            raise ValueError("Runtime task has no immutable RTL fingerprint")
        stages = self.runtime_store.list_stages(run.run_id)
        plugin_version = str(stages[0].plugin_version if stages else "registered")
        return LearningContext(
            design_id=run.task_spec.design_id, design_fingerprint=rtl_sha,
            platform=_learning_identifier(
                run.task_spec.parameters.get("platform"), "unknown-platform"
            ),
            pdk_id=_learning_identifier(
                run.task_spec.parameters.get("platform"), "unknown-pdk"
            ),
            toolchain_id=_learning_identifier(
                f"{run.task_spec.plugin_id}-{plugin_version}", "unknown-toolchain"
            ),
            flow_stage=str(run.task_spec.parameters.get("target_stage") or "finish"),
            metric_parser_version=_learning_identifier(
                metric_parser_version or "web-evidence-v1", "web-evidence-v1"
            ),
        )

    def auto_collect_terminal_run(self, run_id: str) -> dict[str, Any]:
        """Auto-learning hook for the worker: succeeded -> collect; otherwise -> reject."""
        run = self.runtime_store.get_run(run_id)
        if run.status.value not in {"succeeded", "failed", "cancelled", "timed_out"}:
            return {"run_id": run_id, "action": "skipped", "reason": "not terminal"}
        if run.task_spec.plugin_id == "rtlscout" and run.task_spec.inputs.get("mode") == "specir-v2":
            return self.record_rtlscout_candidate_run(run_id)
        if run.task_spec.plugin_id == "rtl-verify":
            return self.record_rtl_verification_run(run_id)
        if run.task_spec.plugin_id == "rtl-sim":
            return self.record_rtl_simulation_run(run_id)
        if run.task_spec.plugin_id == "rtl-mutation":
            return self.record_rtl_mutation_run(run_id)
        if run.task_spec.plugin_id == "rtl-formal":
            return self.record_rtl_formal_run(run_id)
        if run.task_spec.plugin_id == "orfs" and run.task_spec.labels.get("candidate_id"):
            return self.record_rtl_implementation_run(run_id)
        owner_id = str(run.task_spec.labels.get("owner_id") or "system-auto")
        project_id = str(run.task_spec.project_id or "openroad-platform")
        try:
            context = self._learning_context_for_run(run)
        except ValueError as exc:
            return {"run_id": run_id, "action": "skipped",
                    "reason": f"no learning context: {exc}"}
        if run.status.value == "succeeded":
            receipt = self.learning_collector.collect(
                run_id, context, tenant_id=owner_id, project_id=project_id)
            return {"run_id": run_id, "action": "collect",
                    "status": receipt.status,
                    "observation_id": receipt.observation_id,
                    "reason": receipt.reason}
        attempts = [attempt for stage in self.runtime_store.list_stages(run_id)
                    for attempt in self.runtime_store.list_attempts(stage.stage_run_id)]
        failure = attempts[-1].failure if attempts else None
        reason = str(failure or run.terminal_reason or "run did not succeed")
        rejection_id = self.learning_collector.reject(
            run_id, context, tenant_id=owner_id, project_id=project_id,
            run_status=run.status.value, reason=reason)
        return {"run_id": run_id, "action": "reject", "rejection_id": rejection_id}

    def record_rtlscout_candidate_run(self, run_id: str) -> dict[str, Any]:
        """Materialize only a Runtime-succeeded RTLScout-v2 artifact as a candidate."""
        run = self.runtime_store.get_run(run_id)
        if run.task_spec.plugin_id != "rtlscout" or run.task_spec.inputs.get("mode") != "specir-v2":
            raise ValueError("Runtime run is not an RTLScout-v2 SpecIR task")
        if run.status.value not in {"succeeded", "failed", "cancelled", "timed_out"}:
            return {"run_id": run_id, "action": "skipped", "reason": "not terminal"}
        if run.status.value != "succeeded":
            return {"run_id": run_id, "action": "rejected", "reason": run.status.value}
        view = self.runtime.describe(run_id)
        attempts = [item for stage in view["stages"] for item in stage["attempts"]
                    if item["status"] == "succeeded"]
        terminal = attempts[-1] if attempts else None
        rtl = next((item for item in (terminal or {}).get("artifacts", []) if item["kind"] == "rtl"), None)
        if rtl is None:
            raise ValueError("RTLScout-v2 success lacks RTL artifact")
        source = Path(terminal["workspace"]) / rtl["store_key"]
        if not source.is_file() or _sha256(source) != rtl["sha256"]:
            raise ValueError("RTLScout Runtime artifact is missing or changed")
        destination = self.rtl_candidate_root / f"{rtl['sha256']}.sv"
        if not destination.exists():
            shutil.copy2(source, destination)
        spec_id = str(run.task_spec.labels["spec_id"])
        verification_id = str(run.task_spec.labels["verification_id"])
        candidate = RTLCandidate(
            candidate_id=f"candidate-{uuid.uuid4().hex}", spec_id=spec_id,
            verification_id=verification_id,
            rtl_artifact_ref=f"artifact:rtl-candidate:{rtl['sha256']}",
            generator="rtlscout-v2", provenance={"runtime_run_id": run_id,
                "rtl_sha256": rtl["sha256"], "specir_input": True,
                "oracle_provenance": run.task_spec.inputs.get("oracle_provenance", {})},
        )
        try:
            self.rtl_frontend.add_candidate(candidate)
        except ValueError as exc:
            if "already exists" not in str(exc):
                raise
        return {"run_id": run_id, "action": "rtlscout_candidate", "candidate_id": candidate.candidate_id,
                "rtl_sha256": rtl["sha256"], "authority": "Runtime-produced RTLScout-v2 artifact"}

    def record_rtl_verification_run(self, run_id: str) -> dict[str, Any]:
        """Project a terminal Runtime verification attempt into candidate lineage.

        This is intentionally separate from learning observations: compile/lint
        is a frontend gate, not an optimization sample or a functional proof.
        """
        run = self.runtime_store.get_run(run_id)
        if run.task_spec.plugin_id != "rtl-verify":
            raise ValueError("Runtime run is not an RTL verification task")
        if run.status.value not in {"succeeded", "failed", "cancelled", "timed_out"}:
            return {"run_id": run_id, "action": "skipped", "reason": "not terminal"}
        candidate_id = str(run.task_spec.labels.get("candidate_id") or "")
        if not candidate_id:
            raise ValueError("RTL verification task has no candidate_id label")
        view = self.runtime.describe(run_id)
        attempts = [attempt for stage in view["stages"] for attempt in stage["attempts"]]
        terminal = attempts[-1] if attempts else None
        artifact = next((item for item in (terminal or {}).get("artifacts", [])
                         if item["kind"] == "verification_report"), None)
        if artifact:
            evidence_ref, evidence_sha = f"artifact:runtime:{run_id}:{artifact['artifact_id']}", artifact["sha256"]
        else:
            serialized = json.dumps({"run_id": run_id, "status": run.status.value,
                                     "failure": (terminal or {}).get("failure")}, sort_keys=True)
            evidence_ref, evidence_sha = f"source:runtime:{run_id}", _sha256_text(serialized)
        status = "passed" if run.status.value == "succeeded" else "failed"
        try:
            self.rtl_frontend.add_check(
                check_id=f"rtlverify-{run_id}", candidate_id=candidate_id,
                check_kind="compile_lint", status=status, evidence_ref=evidence_ref,
                evidence_sha256=evidence_sha,
                detail={"run_id": run_id, "runtime_status": run.status.value,
                        "failure": (terminal or {}).get("failure"),
                        "functional_status": "not_evaluated"},
            )
        except ValueError as exc:
            if "already exists" not in str(exc):
                raise
        return {"run_id": run_id, "action": "rtl_candidate_check", "status": status,
                "candidate_id": candidate_id}

    def record_rtl_simulation_run(self, run_id: str) -> dict[str, Any]:
        """Project a frozen-testbench Runtime result into the functional gate."""
        run = self.runtime_store.get_run(run_id)
        if run.task_spec.plugin_id != "rtl-sim":
            raise ValueError("Runtime run is not an RTL simulation task")
        if run.status.value not in {"succeeded", "failed", "cancelled", "timed_out"}:
            return {"run_id": run_id, "action": "skipped", "reason": "not terminal"}
        candidate_id = str(run.task_spec.labels.get("candidate_id") or "")
        if not candidate_id:
            raise ValueError("RTL simulation task has no candidate_id label")
        view = self.runtime.describe(run_id)
        attempts = [attempt for stage in view["stages"] for attempt in stage["attempts"]]
        terminal = attempts[-1] if attempts else None
        artifact = next((item for item in (terminal or {}).get("artifacts", [])
                         if item["kind"] == "simulation_report"), None)
        if artifact:
            evidence_ref = f"artifact:runtime:{run_id}:{artifact['artifact_id']}"; evidence_sha = artifact["sha256"]
        else:
            serialized = json.dumps({"run_id": run_id, "status": run.status.value,
                                     "failure": (terminal or {}).get("failure")}, sort_keys=True)
            evidence_ref, evidence_sha = f"source:runtime:{run_id}", _sha256_text(serialized)
        status = "passed" if run.status.value == "succeeded" else "failed"
        try:
            self.rtl_frontend.add_check(
                check_id=f"rtlsim-{run_id}", candidate_id=candidate_id,
                check_kind="simulation", status=status, evidence_ref=evidence_ref,
                evidence_sha256=evidence_sha,
                detail={"run_id": run_id, "runtime_status": run.status.value,
                        "frozen_testbench": True, "failure": (terminal or {}).get("failure")},
            )
        except ValueError as exc:
            if "already exists" not in str(exc):
                raise
        return {"run_id": run_id, "action": "rtl_candidate_functional_check", "status": status,
                "candidate_id": candidate_id}

    def record_rtl_mutation_run(self, run_id: str) -> dict[str, Any]:
        """Register a mutation score as verification-quality evidence, not correctness."""
        run = self.runtime_store.get_run(run_id)
        if run.task_spec.plugin_id != "rtl-mutation":
            raise ValueError("Runtime run is not an RTL mutation task")
        if run.status.value not in {"succeeded", "failed", "cancelled", "timed_out"}:
            return {"run_id": run_id, "action": "skipped", "reason": "not terminal"}
        candidate_id = str(run.task_spec.labels.get("candidate_id") or "")
        view = self.runtime.describe(run_id); attempts = [a for s in view["stages"] for a in s["attempts"]]
        terminal = attempts[-1] if attempts else None
        artifact = next((x for x in (terminal or {}).get("artifacts", []) if x["kind"] == "mutation_report"), None)
        ref, digest = ((f"artifact:runtime:{run_id}:{artifact['artifact_id']}", artifact["sha256"])
                       if artifact else (f"source:runtime:{run_id}", _sha256_text(json.dumps({"run_id": run_id, "status": run.status.value}, sort_keys=True))))
        detail = {"run_id": run_id, "runtime_status": run.status.value}
        if artifact and terminal:
            path = Path(terminal["workspace"]) / artifact["store_key"]
            if path.is_file() and _sha256(path) == artifact["sha256"]:
                detail["mutation"] = json.loads(path.read_text(encoding="utf-8"))
        status = "passed" if detail.get("mutation", {}).get("eligible") is True else "failed"
        self.rtl_frontend.add_check(check_id=f"rtlmutation-{run_id}", candidate_id=candidate_id,
                                    check_kind="mutation_quality", status=status,
                                    evidence_ref=ref, evidence_sha256=digest, detail=detail)
        return {"run_id": run_id, "action": "rtl_mutation_quality", "status": status, "candidate_id": candidate_id}

    def record_rtl_formal_run(self, run_id: str) -> dict[str, Any]:
        run=self.runtime_store.get_run(run_id)
        if run.task_spec.plugin_id!="rtl-formal": raise ValueError("Runtime run is not an RTL formal task")
        if run.status.value not in {"succeeded","failed","cancelled","timed_out"}: return {"run_id":run_id,"action":"skipped","reason":"not terminal"}
        candidate_id=str(run.task_spec.labels.get("candidate_id") or "")
        if not candidate_id: raise ValueError("RTL formal task has no candidate_id label")
        view=self.runtime.describe(run_id); attempts=[a for s in view["stages"] for a in s["attempts"]]; terminal=attempts[-1] if attempts else None
        artifact=next((x for x in (terminal or {}).get("artifacts",[]) if x["kind"]=="formal_report"),None)
        ref,digest=(f"artifact:runtime:{run_id}:{artifact['artifact_id']}",artifact["sha256"]) if artifact else (f"source:runtime:{run_id}",_sha256_text(json.dumps({"run_id":run_id,"status":run.status.value},sort_keys=True)))
        try: self.rtl_frontend.add_check(check_id=f"rtlformal-{run_id}",candidate_id=candidate_id,check_kind="formal",status="passed" if run.status.value=="succeeded" else "failed",evidence_ref=ref,evidence_sha256=digest,detail={"run_id":run_id,"runtime_status":run.status.value,"bounded":True})
        except ValueError as exc:
            if "already exists" not in str(exc): raise
        return {"run_id":run_id,"action":"rtl_candidate_functional_check","status":"passed" if run.status.value=="succeeded" else "failed","candidate_id":candidate_id}

    def record_rtl_implementation_run(self, run_id: str) -> dict[str, Any]:
        """Attach authoritative ORFS terminal evidence to its RTL candidate."""
        run = self.runtime_store.get_run(run_id)
        if run.task_spec.plugin_id != "orfs" or not run.task_spec.labels.get("candidate_id"):
            raise ValueError("Runtime run is not a candidate-linked ORFS task")
        if run.status.value not in {"succeeded", "failed", "cancelled", "timed_out"}:
            return {"run_id": run_id, "action": "skipped", "reason": "not terminal"}
        candidate_id = str(run.task_spec.labels["candidate_id"])
        view = self.runtime.describe(run_id)
        attempts = [attempt for stage in view["stages"] for attempt in stage["attempts"]]
        terminal = attempts[-1] if attempts else None
        artifacts = (terminal or {}).get("artifacts", [])
        evidence = next((item for item in artifacts if item["kind"] in {"report", "metrics", "log"}), None)
        if evidence:
            ref, digest = f"artifact:runtime:{run_id}:{evidence['artifact_id']}", evidence["sha256"]
        else:
            payload = json.dumps({"run_id": run_id, "status": run.status.value,
                                  "failure": (terminal or {}).get("failure")}, sort_keys=True)
            ref, digest = f"source:runtime:{run_id}", _sha256_text(payload)
        try:
            self.rtl_frontend.add_check(
                check_id=f"rtlppa-{run_id}", candidate_id=candidate_id, check_kind="ppa",
                status="passed" if run.status.value == "succeeded" else "failed",
                evidence_ref=ref, evidence_sha256=digest,
                detail={"run_id": run_id, "runtime_status": run.status.value,
                        "terminal_reason": run.terminal_reason,
                        "authority": "Runtime/ORFS terminal record; no significance claim"},
            )
        except ValueError as exc:
            if "already exists" not in str(exc):
                raise
        return {"run_id": run_id, "action": "rtl_candidate_ppa_check",
                "status": "passed" if run.status.value == "succeeded" else "failed",
                "candidate_id": candidate_id}

    def collect_runtime_learning(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        owner_id = str(payload.get("owner_id") or "local-user")
        run = self._authorize_runtime(
            run_id, owner_id, include_legacy=payload.get("include_legacy") is True
        )
        context = self._learning_context_for_run(
            run, metric_parser_version=payload.get("metric_parser_version"))
        receipt = self.learning_collector.collect(
            run_id, context, tenant_id=owner_id,
            project_id=str(payload.get("project_id") or run.task_spec.project_id),
        )
        return dataclasses.asdict(receipt)

    def list_learning_observations(self, query: dict[str, list[str]]) -> dict[str, Any]:
        tenant_id = (query.get("tenant_id") or [""])[0]
        project_id = (query.get("project_id") or [""])[0]
        observations = self.tenant_learning_store.list(tenant_id, project_id)
        return {"tenant_id": tenant_id, "project_id": project_id,
                "observations": [item.to_dict() for item in observations],
                "source": "observed", "shared": False}

    def auto_optimize(self, payload: dict[str, Any]) -> dict[str, Any]:
        """One-click: pick the design with the most same-context observations,
        create an optimization study, run BO propose, and generate a recommendation."""
        import uuid as _uuid
        owner_id = str(payload.get("owner_id") or "local-user")
        observations = (self.tenant_learning_store.list_all()
                        if owner_id == "local-user" else
                        self.tenant_learning_store.list(owner_id, "openroad-platform"))
        if len(observations) < 4:
            raise ValueError("需要至少 4 条观测才能建立优化研究（当前 %d 条）" % len(observations))
        groups = {}
        for item in observations:
            groups.setdefault(item.context.fingerprint, []).append(item)
        best = max(groups.values(), key=len)
        if len(best) < 4:
            raise ValueError("同一设计/流程的观测不足（需要 ≥4，最大组仅 %d 条）" % len(best))
        ctx = best[0].context
        from openroad_platform_contracts import (  # noqa: E402
            ObjectiveSpec, OptimizationStudy, ParameterSpec,
        )
        # Do not silently collapse a design-space study into one knob.  A
        # parameter is eligible only when it was actually varied in the
        # observed, same-context evidence; fixed labels are context, not an
        # optimisation action.  The downstream BO and shadow policies can
        # therefore reason over coupled utilisation/density/etc. choices.
        values_by_parameter: dict[str, list[float]] = {}
        for item in best:
            for name, value in item.parameters.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    values_by_parameter.setdefault(name, []).append(float(value))
        parameter_space = tuple(
            ParameterSpec(name=name, lower=min(values), upper=max(values))
            for name, values in sorted(values_by_parameter.items())
            if len(set(values)) >= 2
        )
        if not parameter_space:
            raise ValueError("同一上下文中没有至少一个被实际改变的数值参数，不能伪造优化空间")
        study = OptimizationStudy(
            study_id=f"study-{_uuid.uuid4().hex[:16]}",
            design_id=ctx.design_id, context_fingerprint=ctx.fingerprint,
            parameter_space=parameter_space,
            objectives=(ObjectiveSpec(metric_name="area", direction="min"),),
            max_runs=64, seed=1,
        )
        study_id = self.optimization_store.create(study)
        for item in best:
            self.optimization_store.add_observation(study_id, item)
        from openroad_platform_analysis import (  # noqa: E402
            MultiObjectiveBayesianOptimizer,
        )
        study_obs = self.optimization_store.observations(study_id)
        proposal = MultiObjectiveBayesianOptimizer(
            pool_size=512, exploration=0.05).propose(study, study_obs)
        self.optimization_store.save_proposal(proposal)
        result = self.create_recommendation(
            study_id, {"owner_id": owner_id, "worst_case_cost_seconds": 1800})
        result["study_id"] = study_id
        result["observation_count"] = len(best)
        return result

    def export_si2(self, owner_id: str, project_id: str) -> dict[str, Any]:
        """Export the tenant's observations in Si2 AI-for-EDA style structure."""
        observations = self.tenant_learning_store.list(owner_id, project_id)
        records = []
        for obs in observations:
            ctx = obs.context
            metrics = obs.metrics or {}
            records.append({
                "record_type": "si2_ai_eda_observation_v1",
                "record_id": obs.observation_id,
                "design": {"design_id": ctx.design_id,
                           "design_fingerprint": ctx.design_fingerprint},
                "flow": {"flow_stage": ctx.flow_stage},
                "pdk_library": {"pdk_id": ctx.pdk_id, "platform": ctx.platform,
                                "toolchain_id": ctx.toolchain_id},
                "netlist": {"cross_tier_nets": metrics.get("cross_tier_nets")},
                "timing": {"wns_ns": metrics.get("wns_ns"),
                           "tns_ns": metrics.get("tns_ns")},
                "physical": {"area_um2": metrics.get("area"),
                             "core_utilization_pct": metrics.get("core_utilization_pct")},
                "verification": {"drc_errors": metrics.get("drc_errors"),
                                 "evidence_sha256": metrics.get("artifact_fingerprint")},
                "source": "observed",
            })
        return {"tenant_id": owner_id, "project_id": project_id,
                "schema": "si2_ai_eda_observation_v1",
                "record_count": len(records), "records": records}

    def workspace_examples(self) -> list[dict[str, Any]]:
        """Static starter examples plus imported ORFS typical designs."""
        examples = list(self.designs.examples())
        for item in self.designs.list(limit=200):
            desc = str(item.get("description") or "")
            if not desc.startswith("[ORFS]"):
                continue
            examples.append({
                "id": item["id"], "name": item["module"],
                "level": "orfs", "description": desc,
                "design_id": item["id"],
            })
        return examples

    def _byok_transport_available(self) -> bool:
        return self.byok_transport_secure

    def run_optimizer_iteration(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Stage 1.1: one OptimizerAgent loop pass over the study observations.

        Planning-only: never executes EDA itself. It produces a reviewable
        plan (required_gate=human_review) plus headroom/attribution/trend and
        records the loop into an AgentTrace for the web dashboard.
        """
        study_id = str(payload.get("study_id") or "").strip()
        if not study_id:
            raise ValueError("study_id is required")
        study = self.optimization_store.get(study_id)
        observations = self.optimization_store.observations(study_id)
        ledger = IterationLedger(Path(os.environ.get(
            "OPENROAD_PLATFORM_ITERATION_LEDGER",
            self.local_state_root / "agent-iterations.jsonl")))
        parameter_bounds = {
            item.name: (float(item.lower), float(item.upper))
            for item in study.parameter_space
        }
        metric = str(payload.get("metric") or study.objectives[0].metric_name)
        direction = str(payload.get("direction") or study.objectives[0].direction)
        max_rounds = int(payload.get("max_rounds", 20))
        agent = OptimizerAgent(
            ledger, trace_store=self.agent_traces,
            parameter_bounds=parameter_bounds, metric=metric,
            direction=direction, max_rounds=max_rounds,
        )
        # Seed the ledger from already-observed runs so the loop continues
        # from real evidence instead of starting from scratch.
        for obs in observations:
            if obs.status != "succeeded":
                continue
            round_no = ledger.latest().round + 1 if ledger.latest() else 1
            ledger.replace_round(round_no, IterationState(
                round=round_no, parameters=obs.parameters, metrics=obs.metrics,
                status="succeeded"))
        trace = self.agent_traces.create(
            "Optimizer 迭代（设计 %s）" % study.design_id, "optimizer")
        result = agent.run_iteration(trace=trace)
        result["study_id"] = study_id
        result["design_id"] = study.design_id
        result["agent_trace_id"] = trace.trace_id
        # Include a stall check so the dashboard can surface redirection.
        trend = AnalysisLayer().dynamic_trend(ledger.read(), metric, direction)
        disruptor = DisruptorAgent()
        result["disruptor"] = disruptor.check(trend)
        self.agent_traces.save(trace)
        return result

    def interaction_shadow_proposal(self, study_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Fit a pairwise-combination advisor from immutable observed runs.

        This endpoint deliberately has no Runtime submission path.  Its purpose
        is to make an observed compound condition visible to a reviewer before
        the separate factorial/holdout gate decides whether it is reusable.
        """
        study = self.optimization_store.get(study_id)
        observations = self.optimization_store.observations(study_id)
        from openroad_platform_analysis import (  # noqa: E402
            OfflineInteractionQShadowPolicy, build_trajectory,
        )
        if len(observations) < 5:
            raise ValueError("组合条件 shadow policy 至少需要 5 条同上下文观测")
        trajectories = build_trajectory(
            observations, study.objectives,
            trajectory_id=f"interaction-{study_id}",
        )
        candidate_actions = payload.get("candidate_actions")
        if not isinstance(candidate_actions, list) or not candidate_actions:
            raise ValueError("candidate_actions must be a non-empty list of bounded parameter combinations")
        declared = {item.name for item in study.parameter_space}
        normalized: list[dict[str, float]] = []
        for action in candidate_actions:
            if not isinstance(action, dict) or set(action) != declared:
                raise ValueError("each candidate action must set exactly the study parameter combination")
            row: dict[str, float] = {}
            for parameter in study.parameter_space:
                value = action.get(parameter.name)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError("candidate action values must be numeric")
                number = float(value)
                if not parameter.lower <= number <= parameter.upper:
                    raise ValueError("candidate action is outside the observed bounded study space")
                row[parameter.name] = number
            normalized.append(row)
        policy = OfflineInteractionQShadowPolicy().fit(trajectories)
        proposal = policy.propose(
            design_id=study.design_id, context_fingerprint=study.context_fingerprint,
            state=trajectories[-1].next_state, candidate_actions=normalized,
            evidence=trajectories[-1].evidence,
        )
        return {
            "proposal": proposal.to_dict(), "trajectory_step_count": len(trajectories),
            "interaction_terms": [list(pair) for pair in policy.interaction_names],
            "evidence_status": "offline_observed_shadow_only",
            "promotion_gate": "repeated factorial evidence plus new-design holdout validation",
            "execution_allowed": False,
        }

    def create_recommendation(self, study_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        owner_id = str(payload.get("owner_id") or "local-user")
        study = self.optimization_store.get(study_id)
        proposals = self.optimization_store.proposals(study_id)
        if not proposals:
            raise ValueError("Study has no optimizer proposal")
        calibration = None
        try:
            calibration = self.calibrate_study(study_id)
        except ValueError:
            calibration = None
        recommendation = build_recommendation(
            study, proposals[-1], self.optimization_store.observations(study_id),
            held_out_error=(calibration["calibration"]["normalized_rmse"]
                            if calibration is not None else None),
            interval_coverage=(calibration["calibration"]["interval_coverage"]
                               if calibration is not None else None),
            worst_case_cost_seconds=float(payload.get("worst_case_cost_seconds", 7200)),
        )
        trace = self.agent_traces.create(
            "参数优化建议（设计 %s）" % study.design_id, "recommendation")
        obs = self.optimization_store.observations(study_id)
        trace.add("memory", "读取经验库",
                  metrics={"观测样本": len(obs),
                           "设计": study.design_id})
        trace.add("think", "贝叶斯优化提议下一组参数",
                  detail=("建议参数: " + ", ".join(
                      "%s=%.2f" % (k, v) for k, v in proposals[-1].parameters.items())),
                  metrics={"acquisition": round(proposals[-1].acquisition_value, 4)})
        step_e = trace.add("evaluate", "校准与置信度评估",
                           metrics={"held_out_rmse":
                                    (round(calibration["calibration"]["normalized_rmse"], 4)
                                     if calibration else None),
                                    "interval_coverage":
                                    (round(calibration["calibration"]["interval_coverage"], 4)
                                     if calibration else None),
                                    "overall_confidence":
                                    round(recommendation.confidence.overall, 3)})
        step_e.detail = "；".join(recommendation.confidence.reasons[:3])[:300]
        self.recommendation_store.save(owner_id, recommendation)
        envelope = automation_envelope(
            recommendation, exact_context=payload.get("exact_context", True) is True,
            study_opt_in=payload.get("study_opt_in") is True,
            budget_available=payload.get("budget_available", True) is True,
        )
        trace.add("result", "推荐方案",
                  metrics={"policy": recommendation.policy_kind,
                           "parameters": recommendation.parameters},
                  detail=("; ".join(recommendation.rationale[:3]))[:400])
        trace.status = "done"
        trace.result = {"recommendation_id":
                        recommendation.recommendation_id,
                        "policy_kind": recommendation.policy_kind,
                        "permission_tier": recommendation.permission_tier}
        self.agent_traces.save(trace)
        return {"recommendation": recommendation.to_dict(), "calibration": calibration,
                "automation_envelope": envelope.to_dict(),
                "agent_trace_id": trace.trace_id}

    def decide_recommendation(self, recommendation_id: str,
                              payload: dict[str, Any]) -> dict[str, Any]:
        owner_id = str(payload.get("owner_id") or "local-user")
        include_legacy = payload.get("include_legacy") is True
        recommendation = self.recommendation_store.get(owner_id, recommendation_id)
        study = self.optimization_store.get(recommendation.study_id)
        registered_owner = self.auth.has_user(owner_id)
        design = (self._owned_design(
            study.design_id, owner_id, include_legacy=include_legacy,
        ) if registered_owner else self.designs.get(study.design_id))
        bounds = {item.name: (item.lower, item.upper) for item in study.parameter_space}
        decision = self.recommendation_store.decide(
            owner_id, recommendation_id, action=str(payload.get("action") or ""),
            parameters=payload.get("parameters"), comment=str(payload.get("comment") or ""),
            parameter_bounds=bounds,
        )
        result: dict[str, Any] = {
            "decision": decision.to_dict(), "campaign_created": False,
            "execution_started": False,
            "next_step": "A rejected decision ends here; an accepted decision may explicitly create a Campaign.",
        }
        if decision.action == "rejected" or payload.get("create_campaign") is not True:
            return result
        proposal = next((item for item in self.optimization_store.proposals(study.study_id)
                         if item.proposal_id == recommendation.proposal_id), None)
        if proposal is None:
            raise ValueError("Recommendation proposal is no longer available")
        plan = proposal_to_experiment_plan(proposal, study)
        candidate = dataclasses.replace(
            plan.candidates[0], parameters=dict(decision.selected_parameters),
            candidate_id=f"candidate-{decision.decision_id.removeprefix('decision-')}",
            source_trial_id=decision.decision_id,
        )
        plan = dataclasses.replace(
            plan, plan_id=f"plan-{decision.decision_id.removeprefix('decision-')}",
            producer="p21-human-confirmed", candidates=(candidate,),
            provenance={**plan.provenance, "recommendation_id": recommendation_id,
                        "decision_id": decision.decision_id,
                        "human_confirmed": True, "predictions_are_canonical_metrics": False},
        )
        base = build_orfs_task(
            (self.designs.rtl_path(
                study.design_id, owner_id=owner_id, include_legacy=include_legacy,
            ) if registered_owner else self.designs.rtl_path(study.design_id)),
            project_id="openroad-platform",
            design_id=study.design_id, top=design["module"],
            target_stage=str(payload.get("target_stage") or "finish"),
            clock_period_ns=_number(payload, "clock_period_ns", 10.0),
            core_utilization_pct=_number(payload, "core_utilization_pct", 10.0),
            place_density=_number(payload, "place_density", 0.45),
            stage_timeout_seconds=int(_number(payload, "stage_timeout_seconds", 3600)),
        )
        base = dataclasses.replace(
            base, task_id=f"human-task-{decision.decision_id.removeprefix('decision-')}",
            labels={**base.labels, "human_decision_id": decision.decision_id,
                    "recommendation_id": recommendation_id, "owner_id": owner_id},
        )
        campaign_id = self.optimization_bridge.create(
            str(payload.get("campaign_name") or f"approved-{study.study_id}"), base, plan,
            max_parallel=1, stage_budgets=payload.get("stage_budgets") or {},
            objective_metric=(study.objectives[0].metric_name if study.objectives else None),
            direction=(study.objectives[0].direction if study.objectives else "min"),
            top_k=1, max_repairs=int(payload.get("max_repairs", 1)),
        )
        if registered_owner:
            self.auth.bind_resource("campaign", campaign_id, owner_id)
        result.update({"campaign_created": True, "campaign_id": campaign_id,
                       "campaign": self.stage_campaigns.describe(campaign_id),
                       "experiment_plan": plan.to_dict(),
                       "next_step": "Explicitly submit the approved Campaign to queue Runtime work."})
        if payload.get("submit") is True:
            run_ids = self.stage_campaigns.ensure_runs(campaign_id)
            result.update({"execution_started": True, "run_ids": list(run_ids),
                           "next_step": "Runtime work is queued; collect only after terminal evidence verification."})
        return result

    def calibrate_study(self, study_id: str) -> dict[str, Any]:
        import numpy as np

        study = self.optimization_store.get(study_id)
        observations = [item for item in self.optimization_store.observations(study_id)
                        if item.status == "succeeded"]
        names = [item.name for item in study.parameter_space]
        objective = study.objectives[0]
        complete = [item for item in observations if objective.metric_name in item.metrics
                    and all(name in item.parameters for name in names)]
        if len(complete) < 3:
            raise ValueError("At least three complete observed samples are required for calibration")
        lows = np.array([item.lower for item in study.parameter_space], dtype=float)
        highs = np.array([item.upper for item in study.parameter_space], dtype=float)
        raw_x = np.array([[item.parameters[name] for name in names] for item in complete], dtype=float)
        x = (raw_x - lows) / (highs - lows)
        y = np.array([item.metrics[objective.metric_name] for item in complete], dtype=float)
        report = calibrate_gp(x, y)
        proposal = self.optimization_store.proposals(study_id)[-1] \
            if self.optimization_store.proposals(study_id) else None
        ood = None
        if proposal is not None and all(name in proposal.parameters for name in names):
            candidate = np.array([proposal.parameters[name] for name in names], dtype=float)
            normalized_candidate = (candidate - lows) / (highs - lows)
            model = GaussianProcessRegressorLite().fit(x, y)
            _, stddev = model.predict(normalized_candidate.reshape(1, -1))
            objective_scale = max(float(np.ptp(y)), float(np.std(y)), 1e-12)
            ood = assess_ood(candidate, raw_x,
                             [(item.lower, item.upper) for item in study.parameter_space],
                             predictive_stddev=float(stddev[0]) / objective_scale).to_dict()
        return {"study_id": study_id, "objective": objective.metric_name,
                "calibration": report.to_dict(), "latest_proposal_ood": ood,
                "source": "observed-only", "execution_started": False}

    def collect_campaign_learning(self, campaign_id: str,
                                  payload: dict[str, Any]) -> dict[str, Any]:
        owner_id = str(payload.get("owner_id") or payload.get("tenant_id") or "local-user")
        include_legacy = payload.get("include_legacy") is True
        registered_owner = self.auth.has_user(owner_id)
        if registered_owner:
            self.get_campaign(
                campaign_id, owner_id=owner_id, include_legacy=include_legacy,
            )
        study_id = str(payload.get("study_id") or "")
        if registered_owner:
            self.get_optimization_study(
                study_id, owner_id=owner_id, include_legacy=include_legacy,
            )
        study = self.optimization_store.get(study_id)
        context = LearningContext(
            design_id=study.design_id,
            design_fingerprint=str(payload.get("design_fingerprint") or ""),
            platform=str(payload.get("platform") or ""),
            pdk_id=str(payload.get("pdk_id") or ""),
            toolchain_id=str(payload.get("toolchain_id") or ""),
            flow_stage=str(payload.get("flow_stage") or "finish"),
            metric_parser_version=str(payload.get("metric_parser_version") or ""),
        )
        if context.fingerprint != study.context_fingerprint:
            raise ValueError("Learning context does not match the optimization study")
        receipts = []
        for member in self.campaign_store.members(campaign_id):
            if member.run_id:
                receipts.append(dataclasses.asdict(self.learning_collector.collect(
                    member.run_id, context, tenant_id=owner_id,
                    project_id=str(payload.get("project_id") or "openroad-platform"),
                )))
        observation_ids = self.optimization_bridge.ingest_terminal(
            campaign_id, context=context,
            exporter=RuntimeEvidenceExporter(self.runtime_store),
            study_store=self.optimization_store, study_id=study_id,
        )
        return {"campaign_id": campaign_id, "study_id": study_id,
                "observation_ids": list(observation_ids), "collection_receipts": receipts,
                "source": "verified-runtime-observed"}

    def list_recommendations(self, owner_id: str) -> dict[str, Any]:
        return {"recommendations": self.recommendation_store.list(owner_id)}

    def craft_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        owner_id = _optional_string(payload.get("owner_id"))
        include_legacy = payload.get("include_legacy") is True
        design_id = str(payload.get("design_id") or "").strip()
        design = self._owned_design(design_id, owner_id, include_legacy=include_legacy)
        plan = build_craft_flow_plan(
            self.designs.rtl_path(design_id, owner_id=owner_id,
                                  include_legacy=include_legacy), project_id=str(payload.get("project_id") or "openroad-platform"),
            design_id=design_id, top=str(payload.get("top") or design["module"]),
            clock=str(payload.get("clock") or "clk"),
            clock_period_ns=_number(payload, "clock_period_ns", 10.0),
            target_stage=str(payload.get("target_stage") or "finish"),
            platform=str(payload.get("platform") or "nangate45"),
            core_utilization_pct=_number(payload, "core_utilization_pct", 10.0),
            place_density=_number(payload, "place_density", 0.45),
            qor_objectives=tuple(str(item) for item in payload.get("qor_objectives", ())),
            required_capabilities=tuple(str(item) for item in payload.get("required_capabilities", ())),
        )
        backend = str(payload.get("backend") or "openroad-orfs")
        task = craft_plan_to_task(plan, backend,
                                  commercial_tool_chain=str(payload.get("commercial_tool_chain") or "synopsys"))
        if owner_id:
            task = dataclasses.replace(task, labels={**task.labels, "owner_id": owner_id})
        result = {"flow_plan": plan.to_dict(), "capability_matrix": craft_capability_matrix(plan),
                  "backend": backend, "task_spec": task.to_dict(), "execution_started": False}
        if payload.get("execute") is True:
            if backend != "openroad-orfs":
                raise ValueError("ImplCraft is script-generation-only in this deployment")
            run = self.runtime_store.find_run_by_task_id(task.task_id)
            if run is None:
                run = self.runtime.submit(task, capability="eda.rtl_to_gds")
            result["execution_started"] = True
            result["runtime"] = self.get_runtime_run(run.run_id, owner_id=owner_id,
                                                     include_legacy=include_legacy)
        return result

    def create_stage_campaign(self, payload: dict[str, Any]) -> dict[str, Any]:
        owner_id = _optional_string(payload.get("owner_id"))
        include_legacy = payload.get("include_legacy") is True
        design_id = str(payload.get("design_id") or "").strip()
        design = self._owned_design(design_id, owner_id, include_legacy=include_legacy)
        objective = str(payload.get("objective") or "balanced")
        flow_mode = str(payload.get("flow_mode") or "campaign")
        if objective not in {"balanced", "timing", "area", "power"}:
            raise ValueError("objective is not allowlisted")
        if flow_mode not in {"campaign", "agent"}:
            raise ValueError("stage-aware campaign flow_mode must be campaign or agent")
        base = build_orfs_task(
            self.designs.rtl_path(design_id, owner_id=owner_id,
                                  include_legacy=include_legacy), project_id="openroad-platform",
            design_id=design_id, top=design["module"],
            target_stage=str(payload.get("target_stage") or "finish"),
            platform_name=str(payload.get("platform") or "nangate45"),
            clock_period_ns=_number(payload, "clock_period_ns", 10.0),
            core_utilization_pct=_number(payload, "core_utilization_pct", 10.0),
            place_density=_number(payload, "place_density", 0.45),
            stage_timeout_seconds=int(_number(payload, "stage_timeout_seconds", 3600)),
            labels={"source": "web-campaign", "objective": objective,
                    "flow_mode": flow_mode,
                    **({"owner_id": owner_id} if owner_id else {})},
        )
        grid = payload.get("parameter_grid") or {}
        if not isinstance(grid, dict):
            raise ValueError("parameter_grid must be an object")
        profile = objective_profile(objective)
        hard_constraints = profile_hard_constraints(objective)
        if not grid:
            grid = profile_grid(base.parameters)
        stage_budgets = payload.get("stage_budgets") or {}
        if not isinstance(stage_budgets, dict):
            raise ValueError("stage_budgets must be an object")
        campaign_id = self.stage_campaigns.create_grid(
            str(payload.get("name") or f"stage-search-{design_id}"), base, grid,
            max_parallel=int(payload.get("max_parallel", 1)),
            stage_budgets=stage_budgets,
            objective_metric=None, direction="min",
            top_k=int(payload.get("top_k", 3)),
            max_repairs=int(payload.get("max_repairs", 2)),
            max_total_runs=int(payload.get("max_total_runs", 64)),
            objectives=profile,
            hard_constraints=hard_constraints,
        )
        if owner_id:
            self.auth.bind_resource("campaign", campaign_id, owner_id)
        return self.get_campaign(
            campaign_id, owner_id=owner_id, include_legacy=include_legacy,
        )

    def create_spec_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        owner_id = _optional_string(payload.get("owner_id"))
        include_legacy = payload.get("include_legacy") is True
        design_id = _optional_string(payload.get("design_id"))
        design = self._owned_design(design_id, owner_id, include_legacy=include_legacy) if design_id else None
        provider = self._spec_provider_from_payload(payload)
        budgets = payload.get("budgets")
        if budgets is not None and not isinstance(budgets, dict):
            raise ValueError("budgets must be an object")
        manager = SpecConversationManager(self.spec_store, provider)
        operation = lambda: manager.create(  # noqa: E731
            message=str(payload.get("message") or ""), design_id=design_id,
            design_context=design, budgets=budgets,
        )
        trace = self.agent_traces.create(
            str(payload.get("message") or "Spec-to-RTL"), "spec-to-rtl")
        trace.add("goal", "目标", detail=str(payload.get("message") or "")[:500])
        trace.add("plan", "解析设计意图与接口", detail="识别功能、时钟、目标平台与阶段…")
        step = trace.start_tool(provider.provider_name,
                                "LLM 生成规范提案（objective / 功能 / 接口 / 假设）")
        _t0 = time.time()
        result = (self._server_spec_call(owner_id, operation)
                  if provider.provider_name == "codex-cli" else operation())
        step.duration_ms = int((time.time() - _t0) * 1000)
        _props = result.get("state") or {}
        trace.finish_tool(
            step, ok=True,
            metrics={"missing_fields": len(_props.get("missing_fields") or []),
                     "ready": bool(_props.get("ready_for_execution"))},
            detail=("objective=%s | top=%s | clock=%.1fns | 假设=%d | 追问=%d" % (
                str(_props.get("objective") or "")[:60],
                str(_props.get("top") or "-"),
                float(_props.get("clock_period_ns") or 0),
                len(_props.get("assumptions") or []),
                len(_props.get("clarification_questions") or [])))[:400])
        trace.add("evaluate", "提案自检",
                  status="ok" if _props.get("ready_for_execution") else "failed",
                  metrics={"missing_fields": list(_props.get("missing_fields") or [])},
                  detail=("字段齐全可进入实现" if _props.get("ready_for_execution")
                          else "缺少字段，等待用户补充"))
        trace.status = "done"
        trace.result = {"session_id": result["session_id"],
                        "ready": bool(_props.get("ready_for_execution")),
                        "next_authority": "RTLScout-v2"}
        self.agent_traces.save(trace)
        result = {**result, "agent_trace_id": trace.trace_id}
        if owner_id:
            self.auth.bind_resource("spec_session", result["session_id"], owner_id)
        if provider.provider_name == "openai-compatible-byok":
            self._spec_provider_bindings[result["session_id"]] = {
                key: str(payload[key]) for key in
                ("owner_id", "session_id", "profile_id", "secret_handle")
            }
        return result

    def get_spec_session(self, session_id: str, *, owner_id: str | None = None,
                         include_legacy: bool = False) -> dict[str, Any]:
        if owner_id and not self.auth.owns_resource(
            "spec_session", session_id, owner_id, include_legacy=include_legacy
        ):
            raise KeyError(f"Unknown spec session: {session_id}")
        session = self.spec_store.get(session_id)
        if session.get("run_id"):
            session["runtime"] = self.get_runtime_run(
                session["run_id"], owner_id=owner_id, include_legacy=include_legacy
            )
        return session

    def get_rtl_lineage(self, spec_id: str, *, owner_id: str | None = None,
                        include_legacy: bool = False) -> dict[str, Any]:
        lineage = self.rtl_frontend.lineage(spec_id)
        self._owned_design(lineage["spec"]["design_id"], owner_id,
                           include_legacy=include_legacy)
        return {
            **lineage,
            "authority": "immutable SpecIR / verification package / candidate lineage",
            "functional_status": "not_evaluated" if not any(
                item["check_kind"] in {"simulation", "formal", "equivalence"}
                and item["status"] == "passed" for item in lineage["checks"]
            ) else "evidence_available",
        }

    def generate_testbench_draft(self, spec_id: str, *, owner_id: str | None = None,
                                 include_legacy: bool = False) -> dict[str, Any]:
        """Return an AI draft only; freezing and approval remain a later action."""
        lineage = self.get_rtl_lineage(spec_id, owner_id=owner_id, include_legacy=include_legacy)
        spec = SpecIR.from_dict(lineage["spec"])
        trace = self.agent_traces.create(f"Testbench 草稿（{spec.top}）", "testbench-draft")
        trace.add("goal", "生成待审核验证草稿", detail="草稿不会作为 oracle 自动冻结或执行")
        step = trace.start_tool("codex-cli", "根据冻结 SpecIR 生成 SystemVerilog testbench 草稿")
        started = time.time()
        try:
            result = _codex_testbench_draft(spec)
        except Exception as exc:
            step.duration_ms = int((time.time() - started) * 1000)
            trace.finish_tool(step, ok=False, detail=str(exc)[:400])
            trace.status = "failed"; trace.result = {"error": str(exc)[:400]}
            self.agent_traces.save(trace)
            raise
        step.duration_ms = int((time.time() - started) * 1000)
        trace.finish_tool(step, ok=True,
                          metrics={"structural_floor_passed": result["structural_floor_passed"]},
                          detail="草稿仍需要独立审核，不能自行成为判卷标准")
        trace.add("evaluate", "结构最低门检查", status="ok" if result["structural_floor_passed"] else "failed",
                  detail=result.get("structural_floor_error") or "DUT/self-check/PASS 结构存在")
        trace.status = "done"; trace.result = {"draft_sha256": result["draft_sha256"],
                                                 "structural_floor_passed": result["structural_floor_passed"]}
        self.agent_traces.save(trace)
        return {"spec_id": spec_id, "agent_trace_id": trace.trace_id, **result}

    def _rtlscout_candidate_path(self, candidate: dict[str, Any]) -> Path:
        ref = str(candidate.get("rtl_artifact_ref") or "")
        prefix = "artifact:rtl-candidate:"
        if not ref.startswith(prefix) or not re.fullmatch(r"[0-9a-f]{64}", ref.removeprefix(prefix)):
            raise ValueError("Candidate does not originate from a managed RTLScout-v2 artifact")
        path = self.rtl_candidate_root / f"{ref.removeprefix(prefix)}.sv"
        if not path.is_file() or _sha256(path) != ref.removeprefix(prefix):
            raise ValueError("Managed RTLScout-v2 artifact is missing or changed")
        return path

    def submit_rtl_verification(self, spec_id: str, *, owner_id: str | None = None,
                                include_legacy: bool = False) -> dict[str, Any]:
        if not self.rtl_verify_readiness["ready"]:
            raise ValueError(self.rtl_verify_readiness["reason"])
        lineage = self.get_rtl_lineage(spec_id, owner_id=owner_id, include_legacy=include_legacy)
        candidate = lineage["candidates"][-1] if lineage["candidates"] else None
        if candidate is None:
            raise ValueError("SpecIR has no RTL candidate")
        design_id, top = lineage["spec"]["design_id"], lineage["spec"]["top"]
        task = build_rtl_verify_task(
            project_id="openroad-platform", design_id=design_id,
            rtl_path=self._rtlscout_candidate_path(candidate),
            top=top, spec_id=spec_id, verification_id=candidate["verification_id"],
            labels={"candidate_id": candidate["candidate_id"], "spec_id": spec_id,
                    **({"owner_id": owner_id} if owner_id else {})},
        )
        run = self.runtime.submit(task, capability="eda.rtl.verify")
        return {"run": self.get_runtime_run(run.run_id, owner_id=owner_id,
                                             include_legacy=include_legacy),
                "candidate_id": candidate["candidate_id"], "execution_started": False}

    def attach_rtl_simulation_oracle(self, spec_id: str, payload: dict[str, Any], *,
                                     owner_id: str | None = None,
                                     include_legacy: bool = False) -> dict[str, Any]:
        """Freeze a user-reviewed testbench and derive a child candidate.

        Testbench text is content addressed on disk and its SHA is retained in
        both the verification package and candidate provenance.  The original
        candidate/package are immutable, so an oracle never silently changes
        the meaning of an already-recorded result.
        """
        lineage = self.get_rtl_lineage(spec_id, owner_id=owner_id, include_legacy=include_legacy)
        parent = lineage["candidates"][-1] if lineage["candidates"] else None
        if parent is None:
            raise ValueError("SpecIR has no RTL candidate")
        spec = SpecIR.from_dict(lineage["spec"])
        source = str(payload.get("testbench_source") or "")
        testbench_top = str(payload.get("testbench_top") or "")
        if not source.strip() or len(source.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("testbench_source must be non-empty and at most 2 MiB")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", testbench_top):
            raise ValueError("testbench_top must be a Verilog identifier")
        _validate_rtlscout_testbench(source, spec.top)
        origin = str(payload.get("oracle_origin") or "")
        reviewed_by = str(payload.get("oracle_reviewed_by") or "").strip()
        if origin not in {"user_authored", "project_existing", "reference_model", "approved_generated"} or not reviewed_by:
            raise ValueError("A user-facing oracle requires declared origin and non-empty reviewer approval")
        digest = _sha256_text(source)
        path = self.verification_oracle_root / f"{digest}.sv"
        if path.exists() and path.read_text(encoding="utf-8") != source:
            raise RuntimeError("verification oracle hash collision")
        if not path.exists():
            path.write_text(source, encoding="utf-8")
        approval_path = self.verification_oracle_root / f"{digest}.{spec_id}.approval.json"
        approval = {
            "sha256": digest, "origin": origin, "reviewed_by": reviewed_by,
            "spec_id": spec_id, "approved_at": datetime.now(timezone.utc).isoformat(),
        }
        if approval_path.exists():
            existing = json.loads(approval_path.read_text(encoding="utf-8"))
            if {key: existing.get(key) for key in ("sha256", "origin", "reviewed_by", "spec_id")} != {
                key: approval[key] for key in ("sha256", "origin", "reviewed_by", "spec_id")
            }:
                raise RuntimeError("verification-oracle approval receipt is immutable")
        else:
            approval_path.write_text(json.dumps(approval, sort_keys=True), encoding="utf-8")
        oracle_ref = f"artifact:verification-oracle:{digest}"
        package = VerificationPackage(
            verification_id=f"verify-{uuid.uuid4().hex}", spec_id=spec_id,
            compile_checks=("verilator-lint", "yosys-check"),
            simulation_oracle_refs=(oracle_ref,),
        )
        candidate = RTLCandidate(
            candidate_id=f"candidate-{uuid.uuid4().hex}", spec_id=spec_id,
            verification_id=package.verification_id,
            rtl_artifact_ref=parent["rtl_artifact_ref"], generator="rtlscout-v2-oracle-attached",
            parent_candidate_ids=(parent["candidate_id"],),
            provenance={"testbench_sha256": digest, "testbench_top": testbench_top,
                        "oracle_ref": oracle_ref, "oracle_origin": origin,
                        "oracle_reviewed_by": reviewed_by},
        )
        self.rtl_frontend.add_verification_package(package)
        self.rtl_frontend.add_candidate(candidate)
        return {"spec_id": spec_id, "candidate_id": candidate.candidate_id,
                "verification_id": package.verification_id, "testbench_sha256": digest,
                "testbench_top": testbench_top,
                "authority": "frozen content-addressed, independently reviewed simulation oracle"}

    def submit_rtl_simulation(self, spec_id: str, payload: dict[str, Any], *,
                              owner_id: str | None = None,
                              include_legacy: bool = False) -> dict[str, Any]:
        if not self.rtl_sim_readiness["ready"]:
            raise ValueError(self.rtl_sim_readiness["reason"])
        lineage = self.get_rtl_lineage(spec_id, owner_id=owner_id, include_legacy=include_legacy)
        candidate = lineage["candidates"][-1] if lineage["candidates"] else None
        if candidate is None:
            raise ValueError("SpecIR has no RTL candidate")
        package = self.rtl_frontend.get_verification_package(candidate["verification_id"])
        ref = package.simulation_oracle_refs[0] if package.simulation_oracle_refs else ""
        prefix = "artifact:verification-oracle:"
        if not ref.startswith(prefix) or not re.fullmatch(r"[0-9a-f]{64}", ref.removeprefix(prefix)):
            raise ValueError("Candidate has no supported frozen simulation oracle")
        testbench = self.verification_oracle_root / f"{ref.removeprefix(prefix)}.sv"
        if not testbench.is_file() or _sha256_text(testbench.read_text(encoding="utf-8")) != ref.removeprefix(prefix):
            raise ValueError("Frozen simulation oracle is missing or changed")
        top = str(candidate["provenance"].get("testbench_top") or "")
        task = build_rtl_sim_task(
            project_id="openroad-platform", design_id=lineage["spec"]["design_id"],
            rtl_path=self._rtlscout_candidate_path(candidate),
            testbench_path=testbench, top=top, spec_id=spec_id,
            verification_id=candidate["verification_id"],
            labels={"candidate_id": candidate["candidate_id"], "spec_id": spec_id,
                    **({"owner_id": owner_id} if owner_id else {})},
        )
        run = self.runtime.submit(task, capability="eda.rtl.simulate")
        return {"run": self.get_runtime_run(run.run_id, owner_id=owner_id,
                                             include_legacy=include_legacy),
                "candidate_id": candidate["candidate_id"], "execution_started": False}

    def submit_rtl_mutation_test(self, spec_id: str, payload: dict[str, Any], *,
                                 owner_id: str | None = None,
                                 include_legacy: bool = False) -> dict[str, Any]:
        """Run mutations against an already frozen, separately reviewed oracle."""
        if not self.rtl_mutation_readiness["ready"]:
            raise ValueError(self.rtl_mutation_readiness["reason"])
        lineage = self.get_rtl_lineage(spec_id, owner_id=owner_id, include_legacy=include_legacy)
        candidate = lineage["candidates"][-1] if lineage["candidates"] else None
        if candidate is None:
            raise ValueError("SpecIR has no RTL candidate")
        if candidate.get("generator") == str(payload.get("verifier_identity") or ""):
            raise ValueError("candidate generator cannot claim independent mutation verification")
        package = self.rtl_frontend.get_verification_package(candidate["verification_id"])
        ref = package.simulation_oracle_refs[0] if package.simulation_oracle_refs else ""
        prefix = "artifact:verification-oracle:"
        if not ref.startswith(prefix):
            raise ValueError("candidate needs a frozen simulation oracle before mutation testing")
        digest = ref.removeprefix(prefix); testbench = self.verification_oracle_root / f"{digest}.sv"
        if not testbench.is_file() or _sha256_text(testbench.read_text(encoding="utf-8")) != digest:
            raise ValueError("frozen simulation oracle is missing or changed")
        top = str(candidate.get("provenance", {}).get("testbench_top") or "")
        task = build_rtl_mutation_task(
            project_id="openroad-platform", design_id=lineage["spec"]["design_id"],
            rtl_path=self._rtlscout_candidate_path(candidate), testbench_path=testbench,
            testbench_top=top, spec_id=spec_id, verification_id=candidate["verification_id"],
            verifier_identity=str(payload.get("verifier_identity") or ""),
            maximum_mutants=max(1, min(int(payload.get("maximum_mutants", 32)), 128)),
            minimum_score=float(payload.get("minimum_score", .80)),
            labels={"candidate_id": candidate["candidate_id"], "spec_id": spec_id,
                    **({"owner_id": owner_id} if owner_id else {})},
        )
        run = self.runtime.submit(task, capability="eda.rtl.mutation_test")
        return {"run": self.get_runtime_run(run.run_id, owner_id=owner_id, include_legacy=include_legacy),
                "candidate_id": candidate["candidate_id"], "execution_started": False,
                "authority": "Runtime mutation evidence; no browser-supplied outcomes"}

    def attach_rtl_formal_oracle(self, spec_id: str, payload: dict[str, Any], *, owner_id: str | None = None, include_legacy: bool = False) -> dict[str, Any]:
        lineage = self.get_rtl_lineage(spec_id, owner_id=owner_id, include_legacy=include_legacy)
        parent = lineage["candidates"][-1] if lineage["candidates"] else None
        source, top = str(payload.get("property_source") or ""), str(payload.get("property_top") or "")
        depth = int(payload.get("depth") or 1)
        if parent is None or not source.strip() or len(source.encode()) > 2 * 1024 * 1024 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", top) or not 1 <= depth <= 64:
            raise ValueError("formal oracle requires parent, bounded property_source, property_top, and depth [1,64]")
        digest = _sha256_text(source); path = self.verification_oracle_root / f"{digest}.formal.sv"
        if not path.exists(): path.write_text(source, encoding="utf-8")
        ref = f"artifact:formal-oracle:{digest}"
        package = VerificationPackage(verification_id=f"verify-{uuid.uuid4().hex}", spec_id=spec_id, compile_checks=("verilator-lint", "yosys-check"), formal_property_refs=(ref,))
        candidate = RTLCandidate(candidate_id=f"candidate-{uuid.uuid4().hex}", spec_id=spec_id, verification_id=package.verification_id, rtl_artifact_ref=parent["rtl_artifact_ref"], generator="rtlscout-v2-formal-oracle-attached", parent_candidate_ids=(parent["candidate_id"],), provenance={"formal_property_sha256":digest,"property_top":top,"formal_depth":depth,"oracle_ref":ref})
        self.rtl_frontend.add_verification_package(package); self.rtl_frontend.add_candidate(candidate)
        return {"spec_id":spec_id,"candidate_id":candidate.candidate_id,"verification_id":package.verification_id,"property_sha256":digest,"property_top":top,"depth":depth,"authority":"frozen content-addressed formal oracle"}

    def submit_rtl_formal(self, spec_id: str, *, owner_id: str | None = None, include_legacy: bool = False) -> dict[str, Any]:
        if not self.rtl_formal_readiness["ready"]: raise ValueError(self.rtl_formal_readiness["reason"])
        lineage=self.get_rtl_lineage(spec_id,owner_id=owner_id,include_legacy=include_legacy); candidate=lineage["candidates"][-1]
        package=self.rtl_frontend.get_verification_package(candidate["verification_id"]); ref=package.formal_property_refs[0] if package.formal_property_refs else ""; prefix="artifact:formal-oracle:"
        if not ref.startswith(prefix): raise ValueError("Candidate has no supported frozen formal oracle")
        digest=ref.removeprefix(prefix); path=self.verification_oracle_root/f"{digest}.formal.sv"
        if not path.is_file() or _sha256_text(path.read_text())!=digest: raise ValueError("Frozen formal oracle is missing or changed")
        task=build_rtl_formal_task(project_id="openroad-platform",design_id=lineage["spec"]["design_id"],rtl_path=self._rtlscout_candidate_path(candidate),property_path=path,property_top=str(candidate["provenance"]["property_top"]),depth=int(candidate["provenance"]["formal_depth"]),spec_id=spec_id,verification_id=candidate["verification_id"],labels={"candidate_id":candidate["candidate_id"],"spec_id":spec_id,**({"owner_id":owner_id} if owner_id else {})})
        run=self.runtime.submit(task,capability="eda.rtl.formal")
        return {"run":self.get_runtime_run(run.run_id,owner_id=owner_id,include_legacy=include_legacy),"candidate_id":candidate["candidate_id"],"execution_started":False}

    def promote_verified_rtl_to_orfs(self, spec_id: str, *, owner_id: str | None = None,
                                     include_legacy: bool = False) -> dict[str, Any]:
        """Submit ORFS only after compile and functional evidence are recorded.

        A lint/synthesis success is a structural gate, never a functional
        proof.  The functional gate is intentionally fail-closed until a
        frozen simulation, formal, or equivalence oracle has produced a
        Runtime-backed pass for this candidate.
        """
        lineage = self.get_rtl_lineage(spec_id, owner_id=owner_id, include_legacy=include_legacy)
        candidate = lineage["candidates"][-1] if lineage["candidates"] else None
        if candidate is None:
            raise ValueError("SpecIR has no RTL candidate")
        passed = [item for item in lineage["checks"]
                  if item["candidate_id"] == candidate["candidate_id"]
                  and item["check_kind"] == "compile_lint" and item["status"] == "passed"]
        if not passed:
            raise ValueError("A successful recorded RTL compile/lint check is required before ORFS promotion")
        functional = [item for item in lineage["checks"]
                      if item["candidate_id"] == candidate["candidate_id"]
                      and item["check_kind"] in {"simulation", "formal", "equivalence"}
                      and item["status"] == "passed"]
        if not functional:
            raise ValueError("A recorded simulation, formal, or equivalence pass is required before ORFS promotion")
        if candidate.get("provenance", {}).get("oracle_origin") == "approved_generated":
            mutation = [item for item in lineage["checks"]
                        if item["candidate_id"] == candidate["candidate_id"]
                        and item["check_kind"] == "mutation_quality" and item["status"] == "passed"]
            if not mutation:
                raise ValueError("An approved generated oracle requires a passing Runtime mutation-quality check before ORFS promotion")
        check = passed[-1]; verify_run_id = str(check["detail"].get("run_id") or "")
        if not verify_run_id:
            raise ValueError("RTL verification check has no Runtime provenance")
        view = self.runtime.describe(verify_run_id)
        attempts = [attempt for stage in view["stages"] for attempt in stage["attempts"]
                    if attempt["status"] == "succeeded"]
        report_attempt = attempts[-1] if attempts else None
        rtl = next((item for item in (report_attempt or {}).get("artifacts", [])
                    if item["kind"] == "rtl"), None)
        if rtl is None:
            raise ValueError("Successful verification Runtime run has no verified RTL artifact")
        rtl_path = Path(report_attempt["workspace"]) / rtl["store_key"]
        if not rtl_path.is_file() or _sha256(rtl_path) != rtl["sha256"]:
            raise ValueError("Verified RTL artifact is missing or changed")
        spec = lineage["spec"]
        task = build_orfs_task(
            rtl_path, project_id="openroad-platform", design_id=spec["design_id"], top=spec["top"],
            clock=spec.get("clock"),
            platform_name=str(spec["constraints"].get("platform") or "nangate45"),
            target_stage=str(spec["constraints"].get("target_stage") or "finish"),
            clock_period_ns=float(spec["constraints"].get("clock_period_ns") or 10),
            core_utilization_pct=float(spec["constraints"].get("core_utilization_pct") or 10),
            place_density=float(spec["constraints"].get("place_density") or .45),
            labels={"source_run_id": verify_run_id, "source_plugin": "rtl-verify",
                    "spec_id": spec_id, "candidate_id": check["candidate_id"],
                    **({"owner_id": owner_id} if owner_id else {})},
        )
        run = self.runtime.submit(task, capability="eda.rtl_to_gds")
        return {"run": self.get_runtime_run(run.run_id, owner_id=owner_id,
                                             include_legacy=include_legacy),
                "source_verification_run_id": verify_run_id,
                "candidate_id": check["candidate_id"], "execution_started": False}

    def add_spec_turn(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        owner_id = _optional_string(payload.get("owner_id"))
        include_legacy = payload.get("include_legacy") is True
        self.get_spec_session(session_id, owner_id=owner_id, include_legacy=include_legacy)
        session = self.spec_store.get(session_id)
        design_id = session.get("design_id")
        design = self._owned_design(design_id, owner_id, include_legacy=include_legacy) if design_id else None
        provider = self._spec_provider_for_session(session_id, session)
        manager = SpecConversationManager(self.spec_store, provider)
        operation = lambda: manager.turn(  # noqa: E731
            session_id, str(payload.get("message") or ""), design_context=design,
        )
        return (self._server_spec_call(owner_id, operation)
                if provider.provider_name == "codex-cli" else operation())

    def register_spec_design(self, session_id: str,
                             payload: dict[str, Any]) -> dict[str, Any]:
        """Removed v1 endpoint retained as a clear migration failure."""
        raise RuntimeError(
            "register-rtl was removed: a specification cannot register model-generated RTL; "
            "use materialize-spec then RTLScout-v2"
        )

    def materialize_specir(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Freeze a reviewed natural-language specification without generating RTL."""
        if payload.get("confirmed") is not True:
            raise ValueError("Explicit SpecIR confirmation is required")
        owner_id = _optional_string(payload.get("owner_id"))
        include_legacy = payload.get("include_legacy") is True
        session = self.get_spec_session(
            session_id, owner_id=owner_id, include_legacy=include_legacy,
        )
        proposal = SpecProposal.from_mapping(session["state"])
        if not proposal.ready_for_execution:
            raise ValueError("Specification still requires clarification")
        base = session_id.removeprefix("spec-")
        design_id = str(session.get("design_id") or f"specdesign-{base}")
        spec = SpecIR(
            spec_id=f"specir-{base}", design_id=design_id, top=str(proposal.top),
            functionality=proposal.functionality, objective=proposal.objective,
            ports=proposal.ports, clock=proposal.clock, reset=proposal.reset,
            constraints={"platform": proposal.target_platform,
                         "target_stage": proposal.target_stage,
                         "clock_period_ns": proposal.clock_period_ns,
                         "core_utilization_pct": proposal.core_utilization_pct,
                         "place_density": proposal.place_density},
            assumptions=proposal.assumptions,
            acceptance_criteria=(
                "RTLScout-v2 candidate must pass frozen functional verification before ORFS.",
                "PPA comparison requires a repeated, same-context protocol.",
            ),
        )
        try:
            self.rtl_frontend.add_spec(spec)
        except ValueError as exc:
            if "already exists" not in str(exc):
                raise
        if owner_id:
            self.auth.bind_resource("rtl_spec", spec.spec_id, owner_id)
        return {
            "session": self.get_spec_session(
                session_id, owner_id=owner_id, include_legacy=include_legacy,
            ),
            "spec": spec.to_dict(),
            "rtl_frontend": self.rtl_frontend.lineage(spec.spec_id),
            "next": "Attach a frozen verification package and submit RTLScout-v2; no RTL exists yet.",
        }

    def _record_v2_rtl_frontend(self, session: dict[str, Any], design: dict[str, Any],
                                rtl_source: str) -> dict[str, Any]:
        """Create a compile-level v2 lineage record after explicit human approval.

        DesignService has already run Yosys to create the registered netlist.
        This records that fact as a *compile* check only; no simulation/formal
        oracle is invented and therefore no functional-pass claim is made.
        """
        state = session["state"]
        analysis = design.get("analysis") or {}
        ports = tuple(
            [PortSpec(name, "input") for name in analysis.get("inputs", ())]
            + [PortSpec(name, "output") for name in analysis.get("outputs", ())]
        )
        if not ports:
            raise ValueError("Registered design has no analyzable interface for SpecIR")
        base = session["session_id"].removeprefix("spec-")
        spec = SpecIR(
            spec_id=f"specir-{base}", design_id=design["id"], top=str(state["top"]),
            functionality=str(state["functionality"]), objective=str(state["objective"]),
            ports=ports, clock=state.get("clock"), reset=state.get("reset"),
            constraints={"platform": state["target_platform"],
                         "target_stage": state["target_stage"],
                         "clock_period_ns": state["clock_period_ns"],
                         "core_utilization_pct": state["core_utilization_pct"],
                         "place_density": state["place_density"]},
            assumptions=tuple(state.get("assumptions") or ()),
            acceptance_criteria=(
                "RTL compiles through the registered Yosys synthesis gate.",
                "Functional simulation/formal checks must be attached before functional pass.",
            ),
        )
        package = VerificationPackage(
            verification_id=f"verify-{base}", spec_id=spec.spec_id,
            compile_checks=("yosys-synthesis",),
        )
        candidate = RTLCandidate(
            candidate_id=f"candidate-{base}-v1", spec_id=spec.spec_id,
            verification_id=package.verification_id,
            rtl_artifact_ref=f"artifact:design:{design['id']}:rtl",
            generator="legacy-spec-provider-reviewed",
            provenance={"spec_session_id": session["session_id"],
                        "rtl_sha256": _sha256_text(rtl_source),
                        "migration": "v2-front-end-bootstrap"},
        )
        try:
            self.rtl_frontend.add_spec(spec)
            self.rtl_frontend.add_verification_package(package)
            self.rtl_frontend.add_candidate(candidate)
            netlist_path = self.designs.source(design["id"], "netlist")
            self.rtl_frontend.add_check(
                check_id=f"check-{base}-yosys", candidate_id=candidate.candidate_id,
                check_kind="yosys-synthesis", status="passed",
                evidence_ref=f"artifact:design:{design['id']}:netlist",
                evidence_sha256=_sha256_text(netlist_path),
                detail={"gate_instances": analysis.get("instance_count"),
                        "functional_status": "not_evaluated"},
            )
        except ValueError as exc:
            # Registration is idempotent after a browser retry; the immutable
            # lineage itself remains unchanged.
            if "already exists" not in str(exc):
                raise
        return self.rtl_frontend.lineage(spec.spec_id)

    def execute_spec_session(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(
            "Spec session execute was removed in v2: use SpecIR → RTLScout-v2 → verification → ORFS"
        )

    def _spec_provider_from_payload(self, payload: dict[str, Any]):
        name = str(payload.get("provider") or "codex-cli")
        model = _optional_string(payload.get("model"))
        if name != "codex-cli":
            raise ValueError(
                "v2 internal mode uses only the platform-managed codex-cli provider"
            )
        return self._spec_provider(name, model)

    def _spec_provider_for_session(self, session_id: str, session: dict[str, Any]):
        if session["provider"] != "codex-cli":
            raise ValueError("This legacy Spec session must be recreated under v2 Codex-only mode")
        return self._spec_provider("codex-cli", session["model"])

    def _server_spec_call(self, owner_id: str | None, operation: Any) -> Any:
        if not self.server_spec_model_ready:
            raise ValueError("The shared server model is temporarily unavailable")
        if not self._server_spec_lock.acquire(blocking=False):
            raise ValueError("The shared server model is busy; retry shortly")
        try:
            if owner_id and self.auth.has_user(owner_id):
                allowed, _ = self.auth.consume_allowance(
                    owner_id, "server-spec-model",
                    limit=self.server_spec_daily_limit,
                )
                if not allowed:
                    raise ValueError(
                        "The shared server-model fair-use allowance resets daily"
                    )
            return operation()
        finally:
            self._server_spec_lock.release()

    @staticmethod
    def _spec_provider(name: str, model: str | None):
        if name == "deterministic":
            return RuleBasedSpecProvider()
        if name in {"codex", "codex-cli"}:
            selected = model or "gpt-5.6-terra"
            if selected not in ALLOWED_MODELS:
                raise ValueError(f"model must be one of: {', '.join(sorted(ALLOWED_MODELS))}")
            return CodexCliSpecProvider(model=selected)
        raise ValueError("provider must be deterministic, codex-cli or openai-compatible-byok")

    def cancel_campaign(self, campaign_id: str, *, owner_id: str | None = None,
                        include_legacy: bool = False) -> dict[str, Any]:
        self.get_campaign(campaign_id, owner_id=owner_id, include_legacy=include_legacy)
        for member in self.campaign_store.members(campaign_id):
            if member.run_id:
                self.runtime_store.request_cancel(member.run_id)
        return self.get_campaign(campaign_id, owner_id=owner_id,
                                 include_legacy=include_legacy)

    def submit_campaign(self, campaign_id: str, *, owner_id: str | None = None,
                         include_legacy: bool = False) -> dict[str, Any]:
        campaign = self.get_campaign(campaign_id, owner_id=owner_id,
                                     include_legacy=include_legacy)
        trace = self.agent_traces.create(
            "批量并行实验（campaign %s）" % campaign_id, "batch-search")
        trace.add("goal", "目标", detail=(
            "设计 %s · %d 个候选参数点" % (
                campaign.get("design_id") or "-",
                len(campaign.get("grid") or {}),
            )))
        try:
            run_ids = self.stage_campaigns.ensure_runs(campaign_id)
        except Exception as exc:
            trace.add("evaluate", "提交失败", status="failed",
                      detail=str(exc)[:300])
            self.agent_traces.save(trace)
            raise
        trace.add("plan", "生成参数网格", metrics={"run_ids": list(run_ids)[:8]})
        trace.add("tool_call", "提交到执行队列", tool="scheduler",
                  detail="run count: %d" % len(run_ids))
        trace.add("result", "批量实验已启动",
                  metrics={"started_runs": len(run_ids)})
        trace.status = "done"
        trace.result = {"campaign_id": campaign_id, "run_ids": list(run_ids)}
        self.agent_traces.save(trace)
        return {
            "campaign": self.get_campaign(
                campaign_id, owner_id=owner_id, include_legacy=include_legacy,
            ),
            "run_ids": list(run_ids),
            "execution_started": True,
            "agent_trace_id": trace.trace_id,
        }

    def submit_design_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        owner_id = _optional_string(payload.get("owner_id"))
        include_legacy = payload.get("include_legacy") is True
        design_id = str(payload.get("design_id") or "").strip()
        design = self._owned_design(design_id, owner_id, include_legacy=include_legacy)
        request = RunRequest(
            rtl_path=str(self.designs.rtl_path(design_id, owner_id=owner_id,
                                               include_legacy=include_legacy)),
            top=_optional_string(payload.get("top")) or design["module"],
            clock=_optional_string(payload.get("clock")),
            platform_name=str(payload.get("platform") or "nangate45"),
            clock_period_ns=_number(payload, "clock_period_ns", 10.0),
            platform=str(payload.get("platform") or "nangate45"),
            target_stage=RunStage(str(payload.get("target_stage") or "finish")),
            core_utilization_pct=_number(payload, "core_utilization_pct", 10.0),
            place_density=_number(payload, "place_density", 0.45),
            stage_timeout_seconds=int(_number(payload, "stage_timeout_seconds", 3600)),
            labels={"source": "design", "design_id": design_id,
                    **({"owner_id": owner_id} if owner_id else {})},
        )
        request.validate()
        return self._serialize_job(self.store.submit(request))

    def submit_runtime_design_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        owner_id = _optional_string(payload.get("owner_id"))
        include_legacy = payload.get("include_legacy") is True
        design_id = str(payload.get("design_id") or "").strip()
        design = self._owned_design(design_id, owner_id, include_legacy=include_legacy)
        objective = str(payload.get("objective") or "balanced")
        flow_mode = str(payload.get("flow_mode") or "baseline")
        if objective not in {"balanced", "timing", "area", "power"}:
            raise ValueError("objective is not allowlisted")
        if flow_mode != "baseline":
            raise ValueError("single Runtime submission requires baseline flow_mode")
        task = build_orfs_task(
            self.designs.rtl_path(design_id, owner_id=owner_id,
                                  include_legacy=include_legacy), project_id="openroad-platform",
            design_id=design_id,
            top=_optional_string(payload.get("top")) or design["module"],
            clock=_optional_string(payload.get("clock")),
            platform_name=str(payload.get("platform") or "nangate45"),
            clock_period_ns=_number(payload, "clock_period_ns", 10.0),
            core_utilization_pct=_number(payload, "core_utilization_pct", 10.0),
            place_density=_number(payload, "place_density", 0.45),
            target_stage=str(payload.get("target_stage") or "finish"),
            stage_timeout_seconds=int(_number(payload, "stage_timeout_seconds", 3600)),
            labels={
                "source": "web-runtime", "design_id": design_id,
                "objective": objective, "flow_mode": flow_mode,
                **({"owner_id": owner_id} if owner_id else {}),
            },
        )
        run = self.runtime.submit(task, capability="eda.rtl_to_gds")
        return self.get_runtime_run(run.run_id, owner_id=owner_id,
                                    include_legacy=include_legacy)

    def compile_task_intent(self, payload: dict[str, Any]) -> dict[str, Any]:
        owner_id = _optional_string(payload.get("owner_id"))
        include_legacy = payload.get("include_legacy") is True
        design_id = str(payload.get("design_id") or "").strip()
        intent = str(payload.get("intent") or "").strip()
        if not design_id:
            raise ValueError("design_id is required")
        design = self._owned_design(design_id, owner_id, include_legacy=include_legacy)
        task = NaturalLanguageTaskCompiler().compile(
            intent, project_id="openroad-platform", design_id=design_id,
            rtl_path=self.designs.rtl_path(design_id, owner_id=owner_id,
                                           include_legacy=include_legacy), top=design["module"],
        )
        return {"task_spec": task.to_dict(), "execution_started": False,
                "notice": "Validated preview only; Runtime submission is a separate action."}

    @staticmethod
    def _serialize_job(job: Any) -> dict[str, Any]:
        return {
            "id": job.id,
            "status": job.status.value,
            "request": job.request.to_dict(),
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "claimed_by": job.claimed_by,
            "heartbeat_at": job.heartbeat_at,
            "result": job.result,
            "error": job.error,
        }


def _find_tool(name: str, fallback: Path) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    return str(fallback) if fallback.is_file() else None


def _validate_rtlscout_testbench(source: str, top: str) -> None:
    """Reject structurally non-executable RTLScout simulation oracles.

    This is intentionally a floor, not a proof of test quality: mutation
    testing and reviewer approval remain necessary before the source is frozen.
    The checks mirror the upstream RTLScout harness, which expects a ``dut``
    instance and a self-checking testbench with an observable pass marker.
    """
    if not re.search(r"\bmodule\s+[A-Za-z_][A-Za-z0-9_$]*\b", source):
        raise ValueError("verification oracle must declare a SystemVerilog testbench module")
    if not re.search(rf"\b{re.escape(top)}\s+dut\s*(?:#\s*\([^;]*?\)\s*)?\(", source, re.S):
        raise ValueError("RTLScout verification oracle must instantiate the SpecIR top as instance dut")
    if not re.search(r"\$(?:fatal|error)\b|\bassert\s*\(", source):
        raise ValueError("verification oracle needs a self-checking failure path ($fatal, $error, or assertion)")
    if "PASS" not in source:
        raise ValueError("RTLScout verification oracle must emit a PASS marker on successful completion")


def _codex_testbench_draft(spec: SpecIR) -> dict[str, Any]:
    """Create an explicitly non-authoritative TB draft with the local Codex CLI.

    The result stays in the HTTP response only.  It is deliberately not an
    oracle artifact and cannot be passed to RTLScout until a user reviews it
    through the separate frozen-oracle submission path.
    """
    executable = shutil.which("codex")
    if not executable:
        raise ValueError("Codex CLI is unavailable; provide an existing or reviewed generated oracle instead")
    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["testbench_source", "assumptions", "coverage_plan", "open_questions"],
        "properties": {
            "testbench_source": {"type": "string", "maxLength": 200000},
            "assumptions": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
            "coverage_plan": {"type": "array", "items": {"type": "string"}, "maxItems": 40},
            "open_questions": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        },
    }
    prompt = (
        "Generate a SystemVerilog TESTBENCH DRAFT, not RTL, from the approved SpecIR below. "
        "It must instantiate the exact top module as instance dut, contain stimulus and self-checking "
        "failure paths ($fatal/error/assertion), print PASS only after checks, and finish. "
        "Never modify the DUT contract, never claim completeness, and list ambiguous behavior in open_questions. "
        "This draft will require independent human review before it becomes a verification oracle. "
        "Return only JSON matching the supplied schema. Do not invoke tools.\n\n"
        f"SPECIR={json.dumps(spec.to_dict(), ensure_ascii=False, sort_keys=True)}"
    )
    with tempfile.TemporaryDirectory(prefix="openroad-tb-draft-") as raw:
        root = Path(raw)
        schema_path, output_path = root / "schema.json", root / "draft.json"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        env = {key: os.environ[key] for key in (
            "HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "TZ", "CODEX_HOME"
        ) if key in os.environ}
        completed = subprocess.run(
            [executable, "exec", "--ephemeral", "--ignore-rules", "--skip-git-repo-check",
             "--sandbox", "read-only", "--model", "gpt-5.6-terra", "--output-schema",
             str(schema_path), "--output-last-message", str(output_path), "--color", "never", "-"],
            input=prompt, cwd=root, env=env, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=180, check=False,
        )
        if completed.returncode != 0 or not output_path.is_file():
            detail = "\n".join((completed.stderr or completed.stdout).splitlines()[-10:])
            raise RuntimeError(detail or "Codex returned no testbench draft")
        try:
            draft = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("Codex returned invalid testbench-draft JSON") from exc
    source = draft.get("testbench_source")
    if not isinstance(source, str) or not source.strip() or len(source.encode("utf-8")) > 2 * 1024 * 1024:
        raise RuntimeError("Codex testbench draft is empty or exceeds the platform size limit")
    structural_error = None
    try:
        _validate_rtlscout_testbench(source, spec.top)
    except ValueError as exc:
        structural_error = str(exc)
    return {
        "draft": {key: draft.get(key, []) for key in ("testbench_source", "assumptions", "coverage_plan", "open_questions")},
        "draft_sha256": _sha256_text(source),
        "structural_floor_passed": structural_error is None,
        "structural_floor_error": structural_error,
        "authority": "unreviewed AI testbench draft; not an oracle and cannot start RTLScout",
        "execution_allowed": False,
    }


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _artifact_presentation(artifact: dict[str, Any]) -> dict[str, str | None]:
    key = str(artifact.get("store_key") or "")
    name = Path(key).name
    kind = str(artifact.get("kind") or "other")
    stage_patterns = (
        ("1_synth", "synth", "Synthesis", "逻辑综合"),
        ("2_floorplan", "floorplan", "Floorplan", "布局规划"),
        ("3_place", "place", "Placement", "布局"),
        ("4_cts", "cts", "Clock tree", "时钟树"),
        ("5_route", "route", "Routing", "布线"),
        ("6_final", "finish", "Final", "最终"),
    )
    stage = next((item for item in stage_patterns if item[0] in name), None)
    stage_id = stage[1] if stage else None
    stage_en = stage[2] if stage else ""
    stage_zh = stage[3] if stage else ""
    exact = {
        "analysis/report.json": ("QoR analysis report", "QoR 分析报告", "report"),
        "plan.json": ("Implementation plan", "实现计划", "report"),
        "toolchain_snapshot.json": ("Toolchain snapshot", "工具链快照", "provenance"),
        "run_result.json": ("Runtime result manifest", "Runtime 结果清单", "provenance"),
        "logs/flow.log": ("Physical-flow execution log", "物理设计执行日志", "log"),
        "config.mk": ("Generated ORFS configuration", "ORFS 生成配置", "configuration"),
        "constraint.sdc": ("Timing constraints", "时序约束", "configuration"),
        "pdn.tcl": ("Power-grid configuration", "电源网络配置", "configuration"),
    }
    match = next((value for suffix, value in exact.items() if key.endswith(suffix)), None)
    if match:
        title_en, title_zh, group = match
    elif kind == "odb":
        title_en, title_zh, group = (
            f"{stage_en} OpenDB database" if stage else "OpenDB database",
            f"{stage_zh} OpenDB 数据库" if stage else "OpenDB 数据库", "implementation",
        )
    elif kind == "def":
        title_en, title_zh, group = (
            f"{stage_en} DEF layout" if stage else "DEF layout",
            f"{stage_zh} DEF 版图" if stage else "DEF 版图", "implementation",
        )
    elif kind == "gds":
        title_en, title_zh, group = "Final GDSII layout", "最终 GDSII 版图", "implementation"
    elif kind == "netlist":
        title_en, title_zh, group = "Final implemented netlist", "最终实现网表", "implementation"
    elif kind == "layout_view":
        title_en, title_zh, group = "Final 2D layout preview", "最终 2D 版图预览", "visualization"
    elif kind == "three_d_view":
        title_en, title_zh, group = "3D layout view", "3D 版图视图", "visualization"
    elif kind == "report":
        title_en, title_zh, group = f"Report · {name}", f"报告 · {name}", "report"
    elif kind == "log":
        title_en, title_zh, group = f"Log · {name}", f"日志 · {name}", "log"
    else:
        title_en, title_zh, group = f"{kind.replace('_', ' ').title()} · {name}", \
            f"{kind.replace('_', ' ')} · {name}", "other"
    return {
        "title_en": title_en, "title_zh": title_zh, "stage": stage_id,
        "group": group, "filename": name,
    }


def _read_worker_heartbeat(path: Path, *, stale_after_seconds: float = 10.0) -> dict[str, Any]:
    offline = {"ready": False, "status": "offline", "updated_at": None,
               "active_run": None}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        updated_at = float(payload.get("updated_at_epoch", 0))
        pid = int(payload.get("pid", 0))
        fresh = 0 <= time.time() - updated_at <= stale_after_seconds
        process_alive = pid > 0
        if process_alive:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                process_alive = False
            except PermissionError:
                process_alive = True
        status = str(payload.get("status") or "offline")
        ready = fresh and process_alive and status in {"idle", "running"}
        return {
            "ready": ready,
            "status": status if ready else "offline",
            "updated_at": payload.get("updated_at"),
            "active_run": payload.get("active_run") if ready else None,
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return offline


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _optional_string(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _learning_identifier(value: Any, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(value or "").strip()).strip("_.:-")
    return (text or fallback)[:128]


def _number(payload: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(payload.get(key, default))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be numeric") from exc


def _bounded_number(payload: dict[str, Any], key: str, default: float,
                    minimum: float, maximum: float, *, integer: bool = False) -> float:
    value = _number(payload, key, default)
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} must be between {minimum:g} and {maximum:g}")
    if integer and not value.is_integer():
        raise ValueError(f"{key} must be an integer")
    return value


def make_handler(state: ApiState) -> type[BaseHTTPRequestHandler]:
    auth_failures: dict[str, list[float]] = {}
    auth_failure_lock = threading.Lock()

    class RequestHandler(BaseHTTPRequestHandler):
        server_version = "OpenROADPlatform/0.1"

        def do_HEAD(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            assets = {
                "/": (WEB_ROOT / "index.html", "text/html; charset=utf-8"),
                "/index.html": (WEB_ROOT / "index.html", "text/html; charset=utf-8"),
                "/assets/app.css": (
                    WEB_ROOT / "assets" / "app.css",
                    "text/css; charset=utf-8",
                ),
                "/assets/app.js": (
                    WEB_ROOT / "assets" / "app.js",
                    "text/javascript; charset=utf-8",
                ),
            }
            asset = assets.get(path)
            if asset is None:
                self._head_error(HTTPStatus.NOT_FOUND)
                return
            file_path, content_type = asset
            if not file_path.is_file():
                self._head_error(HTTPStatus.NOT_FOUND)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(file_path.stat().st_size))
            self._security_headers()
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            session = self._auth_session()
            if session is None and state.no_auth:
                session = state._anonymous_session()
            query = parse_qs(parsed.query)
            developer_all = bool(
                session and session.developer and (query.get("scope") or [""])[0] == "all"
            )
            list_owner = None if developer_all else (session.user_id if session else None)
            direct_owner = None if session and session.developer else (
                session.user_id if session else None
            )
            try:
                if path == "/api/auth/session":
                    self._json(session.public() if session else {"authenticated": False})
                elif path == "/api/health":
                    health = state.health()
                    self._json(health if session else {
                        "ok": health["ok"], "service": health["service"],
                        "execution_ready": health["execution_ready"],
                        "runtime_worker_ready": health["runtime_worker_ready"],
                        "runtime_worker_status": health["runtime_worker_status"],
                        "taiwei_3d_ready": health["taiwei_3d_ready"],
                        "server_spec_model_ready": health["server_spec_model_ready"],
                        "server_spec_model": health["server_spec_model"],
                    })
                elif path == "/api/platform":
                    self._json(state.platform.snapshot(
                        owner_id=session.user_id if session else None,
                        include_legacy=session.legacy_access if session else False,
                        public=session is None,
                    ))
                elif path in {"/", "/index.html"}:
                    self._file(WEB_ROOT / "index.html", "text/html; charset=utf-8")
                elif path == "/assets/app.css":
                    self._file(WEB_ROOT / "assets" / "app.css", "text/css; charset=utf-8")
                elif path == "/assets/app.js":
                    self._file(WEB_ROOT / "assets" / "app.js", "text/javascript; charset=utf-8")
                elif session is None and not state.no_auth:
                    self._error(HTTPStatus.UNAUTHORIZED, "Sign in to access this workspace")
                elif path == "/api/platform/results":
                    result = state.platform.results(
                        owner_id=list_owner,
                        include_legacy=session.legacy_access or developer_all,
                    )
                    if developer_all:
                        users = {item["user_id"]: item["username"]
                                 for item in state.auth.list_users()}
                        for record in result["records"]:
                            record["owner_username"] = users.get(
                                record.get("owner_id"), "Legacy / system"
                            )
                        result["scope"] = "all-users"
                    self._json(result)
                elif path == "/api/developer/users":
                    if not session.developer:
                        self._error(HTTPStatus.FORBIDDEN, "Developer role required")
                    else:
                        self._json({"users": state.auth.list_users()})
                elif path == "/api/platform/evolution":
                    self._json(state.platform.evolution(
                        owner_id=session.user_id, include_legacy=session.legacy_access
                    ))
                elif path == "/api/extensions/edacraft":
                    self._json(edacraft_catalog())
                elif path == "/api/extensions/rtlscout":
                    self._json(state.rtlscout_status())
                elif path == "/api/projects":
                    self._json(state.projects())
                elif path == "/api/designs":
                    no_auth_all = bool(session and session.user_id == "local-user")
                    self._json({"designs": state.designs.list(
                        owner_id=None if (developer_all or no_auth_all) else list_owner,
                        include_legacy=session.legacy_access or developer_all,
                    )})
                elif path == "/api/designs/examples":
                    self._json({"examples": state.workspace_examples()})
                elif re.fullmatch(r"/api/rtl/specs/[^/]+/lineage", path):
                    self._json(state.get_rtl_lineage(
                        unquote(path.split("/")[4]), owner_id=direct_owner,
                        include_legacy=session.legacy_access,
                    ))
                elif re.fullmatch(r"/api/designs/[^/]+/schematic\.svg", path):
                    design_id = unquote(path.split("/")[3])
                    self._text(state.designs.schematic(
                        design_id, owner_id=direct_owner,
                        include_legacy=session.legacy_access,
                    ), "image/svg+xml; charset=utf-8")
                elif re.fullmatch(r"/api/designs/[^/]+/module\.svg", path):
                    design_id = unquote(path.split("/")[3])
                    self._text(state.designs.module_schematic(
                        design_id, owner_id=direct_owner,
                        include_legacy=session.legacy_access,
                    ), "image/svg+xml; charset=utf-8")
                elif re.fullmatch(r"/api/designs/[^/]+/source", path):
                    design_id = unquote(path.split("/")[3])
                    kind = parse_qs(parsed.query).get("kind", ["rtl"])[0]
                    if kind not in {"rtl", "netlist"}:
                        raise ValueError("kind must be rtl or netlist")
                    self._text(state.designs.source(
                        design_id, kind, owner_id=direct_owner,
                        include_legacy=session.legacy_access,
                    ), "text/plain; charset=utf-8")
                elif re.fullmatch(r"/api/designs/[^/]+/design-ir", path):
                    design_id = unquote(path.split("/")[3])
                    self._json(state.designs.design_ir(
                        design_id, owner_id=direct_owner,
                        include_legacy=session.legacy_access,
                    ))
                elif path.startswith("/api/designs/"):
                    self._json(state.designs.get(
                        unquote(path.removeprefix("/api/designs/")), include_source=True,
                        owner_id=direct_owner, include_legacy=session.legacy_access,
                    ))
                elif path == "/api/runs":
                    self._json({"runs": state.list_runs(
                        owner_id=session.user_id, include_legacy=session.legacy_access
                    )})
                elif path == "/api/runtime/runs":
                    design_id = (parse_qs(parsed.query).get("design_id") or [None])[0]
                    self._json(state.list_runtime_runs(
                        owner_id=list_owner,
                        include_legacy=session.legacy_access or developer_all,
                        design_id=design_id,
                    ))
                elif re.fullmatch(r"/api/runtime/runs/[^/]+/artifacts/[^/]+", path):
                    parts = path.split("/")
                    artifact_path, content_type = state.runtime_artifact(
                        unquote(parts[4]), unquote(parts[6]),
                        owner_id=direct_owner, include_legacy=session.legacy_access)
                    self._file(artifact_path, content_type)
                elif re.fullmatch(r"/api/runtime/runs/[^/]+/evidence-ir", path):
                    self._json(state.runtime_evidence_ir(
                        unquote(path.split("/")[4]), owner_id=direct_owner,
                        include_legacy=session.legacy_access))
                elif re.fullmatch(r"/api/runtime/runs/[^/]+/edair", path):
                    self._json(state.runtime_edair(
                        unquote(path.split("/")[4]), owner_id=direct_owner,
                        include_legacy=session.legacy_access))
                elif (match := re.fullmatch(r"/api/runtime/runs/([^/]+)/learning-evidence", path)):
                    query = str(parse_qs(parsed.query).get("query", [""])[0])
                    limit = int(parse_qs(parsed.query).get("limit", ["8"])[0])
                    self._json(state.retrieve_runtime_learning(
                        unquote(match.group(1)), query, owner_id=direct_owner,
                        include_legacy=session.legacy_access, limit=limit,
                    ))
                elif path.startswith("/api/runtime/runs/"):
                    self._json(state.get_runtime_run(
                        unquote(path.removeprefix("/api/runtime/runs/")),
                        owner_id=direct_owner, include_legacy=session.legacy_access))
                elif path == "/api/campaigns":
                    self._json(state.list_campaigns(
                        owner_id=session.user_id, include_legacy=session.legacy_access
                    ))
                elif path == "/api/optimization/studies":
                    self._json(state.list_optimization_studies(
                        owner_id=session.user_id, include_legacy=session.legacy_access
                    ))
                elif path == "/api/knowledge/public":
                    self._json(state.public_knowledge(parse_qs(parsed.query)))
                elif path == "/api/taiwei/technology-matrix":
                    self._json(state.taiwei_technology_matrix())
                elif re.fullmatch(r"/api/four-gate/[^/]+", path):
                    self._json(state.get_four_gate_graph(
                        unquote(path.split("/")[-1]), owner_id=direct_owner,
                        include_legacy=session.legacy_access))
                elif path == "/api/providers":
                    self._json(state.list_provider_profiles(session.user_id))
                elif path == "/api/recommendations":
                    self._json(state.list_recommendations(session.user_id))
                elif path == "/api/export/si2":
                    self._json(state.export_si2(session.user_id, "openroad-platform"))
                elif path == "/api/agent/traces":
                    self._json({"traces": state.agent_traces.list(limit=20)})
                elif path.startswith("/api/agent/traces/"):
                    _tr = state.agent_traces.get(
                        unquote(path.removeprefix("/api/agent/traces/")))
                    self._json({"trace": _tr.to_dict() if _tr else None})
                elif path == "/api/learning/observations":
                    self._json(state.list_learning_observations({
                        "tenant_id": [session.user_id], "project_id": ["openroad-platform"]
                    }))
                elif path.startswith("/api/optimization/studies/"):
                    self._json(state.get_optimization_study(
                        unquote(path.removeprefix("/api/optimization/studies/")),
                        owner_id=session.user_id, include_legacy=session.legacy_access))
                elif re.fullmatch(r"/api/spec/sessions/[^/]+", path):
                    self._json(state.get_spec_session(
                        unquote(path.split("/")[-1]), owner_id=session.user_id,
                        include_legacy=session.legacy_access))
                elif path.startswith("/api/campaigns/"):
                    self._json(state.get_campaign(
                        unquote(path.removeprefix("/api/campaigns/")),
                        owner_id=session.user_id, include_legacy=session.legacy_access))
                elif path.startswith("/api/runs/"):
                    self._json(state.get_run(
                        unquote(path.removeprefix("/api/runs/")),
                        owner_id=session.user_id, include_legacy=session.legacy_access))
                else:
                    self._error(HTTPStatus.NOT_FOUND, "route not found")
            except KeyError as exc:
                self._error(HTTPStatus.NOT_FOUND, str(exc))
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                if path in {"/api/auth/register", "/api/auth/login"}:
                    if self._auth_rate_limited():
                        self._error(HTTPStatus.TOO_MANY_REQUESTS,
                                    "Too many sign-in attempts; wait five minutes")
                        return
                    payload = self._read_json()
                    try:
                        if path.endswith("register"):
                            session, token = state.auth.register(
                                str(payload.get("username") or ""),
                                str(payload.get("password") or ""),
                            )
                        else:
                            session, token = state.auth.login(
                                str(payload.get("username") or ""),
                                str(payload.get("password") or ""),
                            )
                    except ValueError:
                        self._record_auth_failure()
                        raise
                    self._clear_auth_failures()
                    self._json(session.public(), HTTPStatus.CREATED,
                               headers={"Set-Cookie": self._session_cookie(token)})
                    return
                token = self._session_token()
                session = state.auth.resolve(token)
                if path == "/api/auth/logout":
                    state.auth.logout(token)
                    self._json({"authenticated": False}, headers={
                        "Set-Cookie": self._session_cookie("", expire=True)
                    })
                    return
                if session is None and state.no_auth:
                    session = state._anonymous_session()
                if session is None:
                    self._error(HTTPStatus.UNAUTHORIZED, "Sign in to access this workspace")
                    return

                def scoped(payload: dict[str, Any]) -> dict[str, Any]:
                    return {**payload, "owner_id": session.user_id,
                            "tenant_id": session.user_id,
                            "session_id": session.session_id,
                            "include_legacy": session.legacy_access}

                if path == "/api/runs":
                    self._json(state.submit_run(scoped(self._read_json())), HTTPStatus.CREATED)
                    return
                if path == "/api/runs/from-design":
                    self._json(state.submit_design_run(scoped(self._read_json())), HTTPStatus.CREATED)
                    return
                if path == "/api/runtime/runs/from-design":
                    self._json(state.submit_runtime_design_run(scoped(self._read_json())),
                               HTTPStatus.CREATED)
                    return
                if path == "/api/tasks/compile":
                    self._json(state.compile_task_intent(scoped(self._read_json())))
                    return
                if path == "/api/spec/sessions":
                    self._json(state.create_spec_session(scoped(self._read_json())), HTTPStatus.CREATED)
                    return
                if path == "/api/providers":
                    self._json(state.save_provider_profile(scoped(self._read_json())), HTTPStatus.CREATED)
                    return
                if path == "/api/providers/secrets/revoke":
                    self._json(state.revoke_provider_secret(scoped(self._read_json())))
                    return
                if path == "/api/craft/plans":
                    self._json(state.craft_plan(scoped(self._read_json())), HTTPStatus.CREATED)
                    return
                if path == "/api/extensions/taiwei/run":
                    taiwei_result = state.submit_taiwei_design_run(
                        scoped(self._read_json()), owner_id=session.user_id,
                        include_legacy=session.legacy_access)
                    if taiwei_result.get("status") == "guidance_required":
                        self._json(taiwei_result)
                    else:
                        self._json(taiwei_result, HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/extensions/edacraft/([^/]+)/smoke", path)
                if match:
                    self._json(state.submit_edacraft_smoke(
                        unquote(match.group(1)), owner_id=session.user_id), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/extensions/edacraft/([^/]+)/run", path)
                if match:
                    self._json(state.submit_edacraft_run(
                        unquote(match.group(1)), self._read_json(),
                        owner_id=session.user_id,
                        include_legacy=session.legacy_access,
                    ), HTTPStatus.CREATED)
                    return
                if path == "/api/extensions/rtlscout/runs":
                    self._json(state.submit_rtlscout(
                        scoped(self._read_json()), owner_id=session.user_id), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/optimization/studies/([^/]+)/recommend", path)
                if match:
                    study_id = unquote(match.group(1))
                    state.get_optimization_study(
                        study_id, owner_id=session.user_id,
                        include_legacy=session.legacy_access,
                    )
                    self._json(state.create_recommendation(
                        study_id, scoped(self._read_json())), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/optimization/studies/([^/]+)/interaction-shadow", path)
                if match:
                    study_id = unquote(match.group(1))
                    state.get_optimization_study(
                        study_id, owner_id=session.user_id,
                        include_legacy=session.legacy_access,
                    )
                    self._json(state.interaction_shadow_proposal(
                        study_id, scoped(self._read_json())), HTTPStatus.CREATED)
                    return
                if path == "/api/agent/iterate":
                    self._json(state.run_optimizer_iteration(
                        scoped(self._read_json())), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/optimization/studies/([^/]+)/calibrate", path)
                if match:
                    study_id = unquote(match.group(1))
                    state.get_optimization_study(
                        study_id, owner_id=session.user_id,
                        include_legacy=session.legacy_access,
                    )
                    self._json(state.calibrate_study(study_id))
                    return
                match = re.fullmatch(r"/api/recommendations/([^/]+)/decision", path)
                if match:
                    self._json(state.decide_recommendation(
                        unquote(match.group(1)), scoped(self._read_json())), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/runtime/runs/([^/]+)/collect-learning", path)
                if match:
                    self._json(state.collect_runtime_learning(
                        unquote(match.group(1)), scoped(self._read_json())), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/spec/sessions/([^/]+)/turn", path)
                if match:
                    self._json(state.add_spec_turn(
                        unquote(match.group(1)), scoped(self._read_json())))
                    return
                match = re.fullmatch(r"/api/spec/sessions/([^/]+)/execute", path)
                if match:
                    self._json(state.execute_spec_session(
                        unquote(match.group(1)), scoped(self._read_json())), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/spec/sessions/([^/]+)/register-rtl", path)
                if match:
                    self._json(state.register_spec_design(
                        unquote(match.group(1)), scoped(self._read_json())),
                        HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/spec/sessions/([^/]+)/materialize-spec", path)
                if match:
                    self._json(state.materialize_specir(
                        unquote(match.group(1)), scoped(self._read_json())),
                        HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/rtl/specs/([^/]+)/verify", path)
                if match:
                    self._json(state.submit_rtl_verification(
                        unquote(match.group(1)), owner_id=session.user_id,
                        include_legacy=session.legacy_access), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/rtl/specs/([^/]+)/rtlscout", path)
                if match:
                    self._json(state.submit_rtlscout_spec(
                        unquote(match.group(1)), scoped(self._read_json()), owner_id=session.user_id,
                        include_legacy=session.legacy_access), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/rtl/specs/([^/]+)/testbench-draft", path)
                if match:
                    self._read_json()
                    self._json(state.generate_testbench_draft(
                        unquote(match.group(1)), owner_id=session.user_id,
                        include_legacy=session.legacy_access), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/rtl/specs/([^/]+)/simulation-oracle", path)
                if match:
                    self._json(state.attach_rtl_simulation_oracle(
                        unquote(match.group(1)), scoped(self._read_json()), owner_id=session.user_id,
                        include_legacy=session.legacy_access), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/rtl/specs/([^/]+)/formal-oracle", path)
                if match:
                    self._json(state.attach_rtl_formal_oracle(
                        unquote(match.group(1)), scoped(self._read_json()), owner_id=session.user_id,
                        include_legacy=session.legacy_access), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/rtl/specs/([^/]+)/simulate", path)
                if match:
                    self._json(state.submit_rtl_simulation(
                        unquote(match.group(1)), scoped(self._read_json()), owner_id=session.user_id,
                        include_legacy=session.legacy_access), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/rtl/specs/([^/]+)/mutation-test", path)
                if match:
                    self._json(state.submit_rtl_mutation_test(
                        unquote(match.group(1)), scoped(self._read_json()), owner_id=session.user_id,
                        include_legacy=session.legacy_access), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/rtl/specs/([^/]+)/formal", path)
                if match:
                    self._json(state.submit_rtl_formal(unquote(match.group(1)), owner_id=session.user_id,
                        include_legacy=session.legacy_access), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/rtl/specs/([^/]+)/promote-orfs", path)
                if match:
                    self._json(state.promote_verified_rtl_to_orfs(
                        unquote(match.group(1)), owner_id=session.user_id,
                        include_legacy=session.legacy_access), HTTPStatus.CREATED)
                    return
                if path == "/api/optimization/auto":
                    self._json(state.auto_optimize(scoped(self._read_json())), HTTPStatus.CREATED)
                    return
                if path == "/api/evolution/campaigns":
                    self._json(state.start_evolution_campaign(
                        scoped(self._read_json()), owner_id=session.user_id,
                        include_legacy=session.legacy_access), HTTPStatus.CREATED)
                    return
                if path == "/api/evolution/replication-report":
                    self._json(state.replication_qor_report(
                        scoped(self._read_json()), owner_id=session.user_id,
                        include_legacy=session.legacy_access))
                    return
                if path == "/api/evolution/causal-report":
                    self._json(state.causal_qor_report(
                        scoped(self._read_json()), owner_id=session.user_id,
                        include_legacy=session.legacy_access))
                    return
                if path == "/api/evolution/causal-holdout":
                    self._json(state.validate_causal_holdout(
                        scoped(self._read_json()), owner_id=session.user_id,
                        include_legacy=session.legacy_access))
                    return
                if path == "/api/evolution/hypotheses":
                    self._json(state.create_evolution_hypothesis(
                        scoped(self._read_json()), owner_id=session.user_id,
                        include_legacy=session.legacy_access), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/evolution/hypotheses/([^/]+)/assess", path)
                if match:
                    self._json(state.assess_evolution_hypothesis(
                        unquote(match.group(1)), scoped(self._read_json()), owner_id=session.user_id,
                        include_legacy=session.legacy_access), HTTPStatus.CREATED)
                    return
                if path == "/api/research/protocols":
                    self._json(state.preregister_paper_protocol(scoped(self._read_json())), HTTPStatus.CREATED)
                    return
                if path == "/api/research/compare-arms":
                    self._json(state.summarize_paper_arms(scoped(self._read_json())))
                    return
                match = re.fullmatch(r"/api/evolution/campaigns/([^/]+)/advance", path)
                if match:
                    self._json(state.advance_evolution_campaign(
                        unquote(match.group(1)), scoped(self._read_json()), owner_id=session.user_id,
                        include_legacy=session.legacy_access))
                    return
                if path == "/api/four-gate/baseline":
                    self._json(state.begin_four_gate_baseline(
                        scoped(self._read_json()), owner_id=session.user_id,
                        include_legacy=session.legacy_access), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/four-gate/([^/]+)/observe/([^/]+)", path)
                if match:
                    self._read_json()
                    self._json(state.observe_four_gate_run(
                        unquote(match.group(1)), unquote(match.group(2)), owner_id=session.user_id,
                        include_legacy=session.legacy_access), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/four-gate/([^/]+)/propose", path)
                if match:
                    self._json(state.propose_four_gate_action(
                        unquote(match.group(1)), scoped(self._read_json()), owner_id=session.user_id,
                        include_legacy=session.legacy_access), HTTPStatus.CREATED)
                    return
                if path == "/api/four-gate/review-submit":
                    self._json(state.review_four_gate_action(
                        scoped(self._read_json()), owner_id=session.user_id,
                        include_legacy=session.legacy_access), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/four-gate/([^/]+)/measure/([^/]+)", path)
                if match:
                    self._read_json()
                    self._json(state.measure_four_gate_attempt(
                        unquote(match.group(1)), unquote(match.group(2)), owner_id=session.user_id,
                        include_legacy=session.legacy_access), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/four-gate/([^/]+)/decide/([^/]+)", path)
                if match:
                    self._json(state.decide_four_gate_measurement(
                        unquote(match.group(1)), unquote(match.group(2)), scoped(self._read_json()),
                        owner_id=session.user_id, include_legacy=session.legacy_access), HTTPStatus.CREATED)
                    return
                if path == "/api/campaigns/stage-aware":
                    self._json(state.create_stage_campaign(scoped(self._read_json())), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/campaigns/([^/]+)/collect-learning", path)
                if match:
                    self._json(state.collect_campaign_learning(
                        unquote(match.group(1)), scoped(self._read_json())), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/campaigns/([^/]+)/submit", path)
                if match:
                    self._read_json()
                    self._json(state.submit_campaign(
                        unquote(match.group(1)), owner_id=session.user_id,
                        include_legacy=session.legacy_access,
                    ), HTTPStatus.CREATED)
                    return
                if path == "/api/designs/generate":
                    payload = self._read_json()
                    self._json(
                        state.designs.generate(
                            str(payload.get("description") or ""), owner_id=session.user_id),
                        HTTPStatus.CREATED,
                    )
                    return
                if path == "/api/designs/import":
                    payload = self._read_json()
                    self._json(state.designs.import_rtl(
                        filename=str(payload.get("filename") or "design.v"),
                        source=str(payload.get("rtl_source") or ""),
                        description=_optional_string(payload.get("description")),
                        owner_id=session.user_id,
                    ), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/runs/([^/]+)/cancel", path)
                if match:
                    run_id = unquote(match.group(1))
                    state.get_run(run_id, owner_id=session.user_id,
                                  include_legacy=session.legacy_access)
                    self._json(state.cancel_run(run_id))
                    return
                match = re.fullmatch(r"/api/runtime/runs/([^/]+)/cancel", path)
                if match:
                    self._json(state.cancel_runtime_run(
                        unquote(match.group(1)), owner_id=session.user_id,
                        include_legacy=session.legacy_access))
                    return
                match = re.fullmatch(r"/api/campaigns/([^/]+)/cancel", path)
                if match:
                    self._json(state.cancel_campaign(
                        unquote(match.group(1)), owner_id=session.user_id,
                        include_legacy=session.legacy_access))
                    return
                self._error(HTTPStatus.NOT_FOUND, "route not found")
            except (ValueError, json.JSONDecodeError) as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
            except KeyError as exc:
                self._error(HTTPStatus.NOT_FOUND, str(exc))
            except Exception as exc:
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

        def _read_json(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("request body is empty or too large")
            content_type = self.headers.get("Content-Type", "")
            if "application/json" not in content_type:
                raise ValueError("Content-Type must be application/json")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            return payload

        def _session_token(self) -> str | None:
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
            except Exception:
                return None
            item = cookie.get("orp_session")
            return item.value if item else None

        def _auth_session(self) -> AuthSession | None:
            return state.auth.resolve(self._session_token())

        def _session_cookie(self, token: str, *, expire: bool = False) -> str:
            parts = [f"orp_session={token}", "Path=/", "HttpOnly", "SameSite=Lax"]
            forwarded = self.headers.get("X-Forwarded-Proto", "").lower()
            if forwarded == "https":
                parts.append("Secure")
            parts.append("Max-Age=0" if expire else f"Max-Age={7 * 24 * 3600}")
            return "; ".join(parts)

        def _auth_key(self) -> str:
            forwarded = self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For")
            return (forwarded.split(",", 1)[0].strip() if forwarded else self.client_address[0])

        def _auth_rate_limited(self) -> bool:
            cutoff = time.time() - 300
            key = self._auth_key()
            with auth_failure_lock:
                recent = [item for item in auth_failures.get(key, []) if item >= cutoff]
                auth_failures[key] = recent
                return len(recent) >= 8

        def _record_auth_failure(self) -> None:
            with auth_failure_lock:
                auth_failures.setdefault(self._auth_key(), []).append(time.time())

        def _clear_auth_failures(self) -> None:
            with auth_failure_lock:
                auth_failures.pop(self._auth_key(), None)

        def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK,
                  *, headers: dict[str, str] | None = None) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._bytes(body, "application/json; charset=utf-8", status,
                        headers=headers)

        def _text(
            self,
            value: str,
            content_type: str,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            self._bytes(value.encode("utf-8"), content_type, status)

        def _bytes(self, body: bytes, content_type: str, status: HTTPStatus,
                   *, headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self._security_headers()
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path: Path, content_type: str) -> None:
            if not path.is_file():
                self._error(HTTPStatus.NOT_FOUND, "web asset not found")
                return
            body = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self._security_headers()
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: HTTPStatus, message: str) -> None:
            self._json({"ok": False, "error": message}, status)

        def _head_error(self, status: HTTPStatus) -> None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self._security_headers()
            self.end_headers()

        def _security_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            )
            if self.headers.get("X-Forwarded-Proto", "").lower() == "https":
                self.send_header("Strict-Transport-Security", "max-age=31536000")

        def log_message(self, format: str, *args: Any) -> None:
            sys.stderr.write("[web] %s - %s\n" % (self.address_string(), format % args))

    return RequestHandler


def build_server(host: str, port: int, state: ApiState) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(state))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenROAD Platform local web console")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--db", type=Path, default=ROOT / "var" / "platform.db")
    parser.add_argument("--upload-root", type=Path, default=ROOT / "var" / "uploads")
    parser.add_argument("--design-root", type=Path, default=ROOT / "var" / "designs")
    parser.add_argument("--legacy-root", type=Path,
                        default=Path(os.environ.get("ICCAD_ROOT", ROOT.parent / "iccad")))
    local_state = Path(os.environ.get(
        "OPENROAD_PLATFORM_LOCAL_STATE", f"/tmp/openroad-platform-{os.getuid()}"
    ))
    parser.add_argument(
        "--runtime-db", type=Path,
        default=Path(os.environ.get("OPENROAD_PLATFORM_RUNTIME_DB",
                                    local_state / "runtime.db")),
    )
    parser.add_argument(
        "--campaign-db", type=Path,
        default=Path(os.environ.get("OPENROAD_PLATFORM_CAMPAIGN_DB",
                                    local_state / "campaign.db")),
    )
    parser.add_argument(
        "--optimization-db", type=Path,
        default=Path(os.environ.get("OPENROAD_PLATFORM_OPTIMIZATION_DB",
                                    local_state / "optimization.db")),
    )
    parser.add_argument("--auth-db", type=Path, default=ROOT / "var" / "web-auth.db")
    parser.add_argument(
        "--orfs-root",
        type=Path,
        default=Path(os.environ.get("ORFS_ROOT", ROOT.parent / "OpenROAD-flow-scripts")),
    )
    args = parser.parse_args(argv)
    external_url = os.environ.get("OPENROAD_PLATFORM_EXTERNAL_URL", "").strip()
    external_parsed = urlparse(external_url) if external_url else None
    loopback_bind = args.host in {"localhost", "127.0.0.1", "::1"}
    byok_transport_secure = loopback_bind or bool(external_parsed and (
        external_parsed.scheme == "https"
        or external_parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    ))
    state = ApiState(
        args.db,
        args.upload_root,
        args.orfs_root,
        design_root=args.design_root,
        legacy_root=args.legacy_root,
        runtime_db_path=args.runtime_db,
        campaign_db_path=args.campaign_db,
        optimization_db_path=args.optimization_db,
        auth_db_path=args.auth_db,
        byok_transport_secure=byok_transport_secure,
    )
    server = build_server(args.host, args.port, state)
    print(f"OpenROAD Platform: http://{args.host}:{server.server_port}")
    print(f"Queue database: {state.db_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping web console.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
