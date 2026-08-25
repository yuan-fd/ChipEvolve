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
from concurrent.futures import ThreadPoolExecutor
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
    LearningContext, LearningObservation, PortSpec, RTLCandidate, SpecIR, TaskSpec,
    VerificationPackage,
)
from openroad_platform_analysis import (  # noqa: E402
    EvidenceKnowledgeRecordV2, EvidenceRAG, RuntimeEvidenceExporter,
    LearningCollector, OptimizationStudyStore, PublicKnowledgeRegistry,
    TenantLearningStore, load_public_manifest,
    build_run_evidence_ir, evidence_cards_from_run_ir, followup_from_interaction,
    teacher_context_from_holdout,
    replication_report, factorial_interaction_report, validate_holdout_interaction,
    agent_evidence_view, build_design_ir, build_edair, evidence_packet, physical_ir, timing_ir,
    HypothesisLedger, assess_hypothesis, reflection_hypothesis, promote_after_holdout,
    PaperProtocolStore, preregister_protocol, summarize_arm, compare_arms,
    MultiObjectiveBayesianOptimizer, summarize_replicates, relative_utility,
    stalled_decision, diagnosis_packet,
)
from openroad_platform_analysis.parsers.cell_coords import read_def  # noqa: E402
from openroad_platform_analysis.parsers.opensta_timing import parse_opensta_paths  # noqa: E402
from openroad_platform_execution import (  # noqa: E402
    PluginRegistry, ToolchainConfig, build_craft_flow_plan, build_orfs_task,
    build_rtlscout_spec_task,
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
    CodexCliSpecProvider, JobStore, RuntimeStore,
    SpecConversationManager, SpecConversationStore,
    WorkflowRuntime, RTLFrontendStore, ExperimentGraphStore,
    PatchRegistry, SpecProposal, PipelineCheckpointStore,
    objective_profile, profile_grid, profile_hard_constraints,
)
try:  # Supports both `python apps/api/app.py` and package imports in tests.
    from .services import AuthSession, AuthStore, DesignService, PlatformReadModel  # type: ignore[attr-defined]
except ImportError:
    from services import AuthSession, AuthStore, DesignService, PlatformReadModel  # type: ignore[no-redef]


MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 2 * MAX_BODY_BYTES + 64 * 1024


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
        spec_db_path: Path | None = None,
        optimization_db_path: Path | None = None,
        rtl_frontend_db_path: Path | None = None,
        auth_db_path: Path | None = None,
        load_taiwei_plugin: bool = True,
    ):
        self.db_path = db_path.expanduser().resolve()
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
        self.pipeline_checkpoints = PipelineCheckpointStore(state_root / "pipeline-checkpoints.db")
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
        self.auth = AuthStore(auth_db_path or state_root / "web-auth.db")
        # This is an internal/paid-service deployment: model selection is an
        # operator decision, never a browser or API payload option.
        self.server_spec_model = "gpt-5.6-terra"
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
        # These user-space tools are provisioned by the supported non-sudo
        # installer (micromamba + Icarus source build).  A service process
        # must not depend on an interactive shell PATH to find them.
        rtl_tools_root = Path(os.environ.get(
            "OPENROAD_PLATFORM_RTL_TOOLS_ROOT",
            "/share/home/yuanwenjie/.local/opt/openroad-rtl-tools",
        )).expanduser()
        def rtl_tool(name: str, fallback: Path) -> str | None:
            candidate = rtl_tools_root / "bin" / name
            return str(candidate) if candidate.is_file() else _find_tool(name, fallback)
        self.rtl_verify_readiness = {"ready": False, "reason": "Verilator or Yosys unavailable"}
        # RTLScout and the independent lint gate must use one verified
        # Verilator build.  A second user-local Perl wrapper previously hung
        # in `--get-supported`, even though candidate evaluation had already
        # passed with the pinned 5.040 binary.
        pinned_verilator = ROOT / ".tools" / "verilator-5.040" / "bin" / "verilator"
        verifier = (str(pinned_verilator) if pinned_verilator.is_file()
                    else rtl_tool("verilator", ROOT.parent / "bin" / "verilator"))
        if verifier and toolchain.yosys_bin.is_file():
            try:
                manifests.append(rtl_verify_plugin_manifest(
                    verilator_bin=verifier, yosys_bin=toolchain.yosys_bin
                ))
                self.rtl_verify_readiness = {"ready": True, "reason": "Pinned local RTL verification tools are available"}
            except (FileNotFoundError, ValueError) as exc:
                self.rtl_verify_readiness["reason"] = str(exc)
        self.rtl_sim_readiness = {"ready": False, "reason": "Icarus Verilog simulator unavailable"}
        iverilog, vvp = (rtl_tool("iverilog", ROOT.parent / "bin" / "iverilog"),
                          rtl_tool("vvp", ROOT.parent / "bin" / "vvp"))
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
        self.platform = PlatformReadModel(
            designs=self.designs,
            runtime_store=self.runtime_store,
            optimization_store=self.optimization_store,
            knowledge_registry=self.knowledge_registry,
            tenant_learning_store=self.tenant_learning_store,
            pipeline_checkpoints=self.pipeline_checkpoints,
            extension_catalog=edacraft_catalog(),
        )

    def _runtime_environment(self, run) -> dict[str, str]:
        """No user credential is ever injected into a v2 Runtime task."""
        return {}

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
                    "description": "Six-stage ORFS implementation, autonomous BO/GP, evidence, and 2D layout analysis.",
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
                    "description": "Evidence RAG, repeated BO/GP, causal holdout gates, and read-only shadow-policy analysis.",
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
        "entry": "SpecIR + independent verification agent (automatic) or imported frozen oracle",
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

    def submit_rtlscout_spec(self, spec_id: str, payload: dict[str, Any], *,
                             owner_id: str | None = None,
                             include_legacy: bool = False) -> dict[str, Any]:
        """The sole v2 RTL entry: SpecIR plus an independent frozen oracle.

        The oracle may be made automatically by the platform's verification
        agent.  "Frozen" means content-addressed and immutable for this run,
        not "a person must type an approval".  An imported user/project oracle
        is still supported for regression and bring-up work.
        """
        if not self.rtlscout_readiness["ready"]:
            raise ValueError(str(self.rtlscout_readiness["reason"]))
        lineage = self.rtl_frontend.lineage(spec_id)
        owner = self.auth.owner_of("rtl_spec", spec_id)
        if owner_id and owner not in {owner_id, None} and not include_legacy:
            raise KeyError(spec_id)
        spec = SpecIR.from_dict(lineage["spec"])
        testbench = str(payload.get("testbench_source") or "")
        testbench_top = str(payload.get("testbench_top") or "")
        if not testbench.strip() or len(testbench.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("RTLScout-v2 requires a non-empty frozen testbench_source")
        _validate_rtlscout_testbench(
            testbench, spec.top, testbench_top=testbench_top,
            require_upstream_protocol=True,
        )
        origin = str(payload.get("oracle_origin") or "")
        reviewed_by = str(payload.get("oracle_reviewed_by") or "").strip()
        allowed_origins = {"user_authored", "project_existing", "reference_model",
                           "approved_generated", "independent_verifier_agent"}
        if origin not in allowed_origins or not reviewed_by:
            raise ValueError("verification oracle requires a declared origin and producer identity")
        if origin == "independent_verifier_agent" and not reviewed_by.startswith("verification-agent-v2"):
            raise ValueError("automatic oracle must be attributed to verification-agent-v2")
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
        if requested_model and requested_model != "codex-cli:gpt-5.6-terra" and not fixture_mode:
            raise ValueError("v2 uses the fixed platform Codex model: codex-cli:gpt-5.6-terra")
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
            labels={**({"owner_id": owner_id} if owner_id else {})},
            oracle_provenance={"origin": origin, "reviewed_by": reviewed_by,
                               "testbench_top": testbench_top},
        )
        run = self.runtime.submit(task, capability="agent.rtl.generate")
        return {"run": self.get_runtime_run(run.run_id, owner_id=owner_id,
                                              include_legacy=include_legacy),
                "spec_id": spec_id, "verification_id": package.verification_id,
                "testbench_sha256": digest, "execution_started": False,
                "authority": "RTLScout-v2 is the sole RTL candidate producer"}

    def submit_automated_rtlscout(self, spec_id: str, payload: dict[str, Any], *,
                                  owner_id: str | None = None,
                                  include_legacy: bool = False) -> dict[str, Any]:
        """Start the normal automated front-end without a human-written TB.

        A separately prompted verification agent writes the self-checking
        testbench from SpecIR before RTLScout is invoked.  Its artifact is
        immutable and its identity is retained, so the RTL-producing agent
        cannot silently judge its own candidate.  This is an automated quality
        gate, not a mathematical proof of arbitrary natural-language intent.
        """
        lineage = self.rtl_frontend.lineage(spec_id)
        owner = self.auth.owner_of("rtl_spec", spec_id)
        if owner_id and owner not in {owner_id, None} and not include_legacy:
            raise KeyError(spec_id)
        spec = SpecIR.from_dict(lineage["spec"])
        trace = self.agent_traces.create(f"自动验证包（{spec.top}）", "verification-agent")
        trace.add("goal", "独立生成可执行的自检 testbench", detail="不由 RTL 生成 Agent 提供判题脚本")
        step = trace.start_tool("codex-cli", "verification-agent 根据 SpecIR 生成 testbench")
        started = time.time()
        try:
            feedback = _optional_string(payload.get("verification_feedback"))
            generated = (_codex_testbench_draft(spec, feedback=feedback)
                         if feedback else _codex_testbench_draft(spec))
            if not generated["structural_floor_passed"]:
                generated = _codex_testbench_draft(
                    spec,
                    feedback=(
                        "RTLScout protocol preflight rejected the previous draft: "
                        + str(generated.get("structural_floor_error") or "unknown error")
                        + ". The replacement must declare exactly `module tb`, instantiate "
                          "the DUT as `dut`, and emit `TB_SUMMARY total=<N> errors=<M>` "
                          "before PASS."
                    ),
                )
            if not generated["structural_floor_passed"]:
                raise ValueError(generated.get("structural_floor_error") or "verification-agent structural gate failed")
            trace.finish_tool(step, ok=True, metrics={"structural_floor": True},
                              detail="独立 Testbench 已生成并通过 DUT/self-check/PASS 结构检查")
            trace.add("evaluate", "冻结验证包", status="ok",
                      detail="内容哈希固定；后续 RTL 候选只能接受这个验证包")
            trace.status = "done"
            trace.result = {"oracle_sha256": generated["draft_sha256"],
                            "producer": "verification-agent-v2"}
            self.agent_traces.save(trace)
        except Exception as exc:
            step.duration_ms = int((time.time() - started) * 1000)
            trace.finish_tool(step, ok=False, detail=str(exc)[:400])
            trace.status = "failed"; trace.result = {"error": str(exc)[:400]}
            self.agent_traces.save(trace)
            raise
        automated = {**payload, "testbench_source": generated["draft"]["testbench_source"],
                     "testbench_top": generated["draft"]["testbench_top"],
                     "oracle_origin": "independent_verifier_agent",
                     "oracle_reviewed_by": "verification-agent-v2/codex-cli",
                     "verification_agent_trace_id": trace.trace_id}
        result = self.submit_rtlscout_spec(spec_id, automated, owner_id=owner_id,
                                           include_legacy=include_legacy)
        result["automation"] = {
            "verification_agent": "verification-agent-v2",
            "verification_trace_id": trace.trace_id,
            "testbench_sha256": generated["draft_sha256"],
            "next_gates": ["RTLScout candidate", "lint", "simulation", "mutation quality"],
            "human_required": False,
        }
        return result

    def run_automated_rtl_pipeline(self, spec_id: str, payload: dict[str, Any], *,
                                   owner_id: str | None = None,
                                   include_legacy: bool = False) -> dict[str, Any]:
        """Durably drive one pinned candidate from SpecIR to an ORFS baseline.

        The checkpoint is saved before and after every Runtime execution.  A
        repeated request resumes the same pipeline, run IDs, verification
        package, and candidate instead of regenerating a testbench or selecting
        whichever candidate happens to be latest.  Runtime remains the only
        authority for tool outcomes.
        """
        execute_orfs = payload.get("execute_orfs", True) is True
        initial = {
            "status": "running", "spec_id": spec_id, "candidate_id": None,
            "verification_id": None, "steps": [], "boundary": None,
            "execute_orfs": execute_orfs, "rtl_revision": 0,
            "max_revisions": max(0, min(int(payload.get("max_revisions", 2)), 8)),
            "revision_history": [],
        }
        checkpoint = self.pipeline_checkpoints.create_or_get(
            pipeline_kind="rtl-to-orfs-v2", subject_id=spec_id,
            owner_id=owner_id, initial_state=initial,
        )
        if checkpoint["subject_id"] != spec_id:
            raise ValueError("pipeline checkpoint subject mismatch")
        state = checkpoint["state"]
        if bool(state.get("execute_orfs", True)) != execute_orfs:
            raise ValueError("execute_orfs cannot change while resuming a pipeline")
        if state.get("status") in {"baseline_succeeded", "baseline_submitted"}:
            return {**state, "pipeline_id": checkpoint["pipeline_id"],
                    "revision": checkpoint["revision"], "resumed": True,
                    "authority": "all pass/fail outcomes are Runtime-backed"}

        def save() -> None:
            nonlocal checkpoint
            checkpoint = self.pipeline_checkpoints.save(
                checkpoint["pipeline_id"], state,
                expected_revision=checkpoint["revision"],
            )

        def stage_name(name: str) -> str:
            return f"{name}:r{int(state.get('rtl_revision', 0))}"

        def step(name: str) -> dict[str, Any] | None:
            current = stage_name(name)
            return next((item for item in state["steps"] if item["stage"] == current), None)

        def remember_submission(name: str, receipt: dict[str, Any]) -> dict[str, Any]:
            item = {"stage": stage_name(name),
                    "role": name,
                    "rtl_revision": int(state.get("rtl_revision", 0)),
                    "run_id": str(receipt["run"]["run"]["run_id"]),
                    "status": str(receipt["run"]["run"].get("status") or "queued")}
            state["steps"].append(item)
            save()
            return item

        def execute(item: dict[str, Any], failure_boundary: str, *,
                    require_collected_pass: bool = False) -> bool:
            run_id = str(item["run_id"])
            try:
                run = self.runtime_store.get_run(run_id)
            except KeyError:
                # Unit orchestration fakes may supply only the Runtime return
                # object.  Production submissions are always persisted first.
                run = self.runtime.execute_once(run_id)
            else:
                if run.status.value not in {"succeeded", "failed", "cancelled", "timed_out"}:
                    run = self.runtime.execute_once(run_id)
            collected = item.get("collection")
            if not isinstance(collected, dict):
                collected = self.auto_collect_terminal_run(run_id)
            item.update({"status": run.status.value, "collection": collected})
            if collected.get("candidate_id") and not state.get("candidate_id"):
                state["candidate_id"] = collected["candidate_id"]
            runtime_failed = run.status.value != "succeeded"
            quality_gate_failed = (
                require_collected_pass and collected.get("status") != "passed"
            )
            if runtime_failed or quality_gate_failed:
                item["boundary"] = failure_boundary
                if quality_gate_failed:
                    item["gate_failure"] = {
                        "kind": "collected_quality_gate",
                        "expected": "passed",
                        "observed": collected.get("status"),
                    }
                state.update({"status": "stopped", "boundary": failure_boundary})
                save()
                return False
            save()
            return True

        def revise_or_stop(boundary: str, failed_step: dict[str, Any]) -> dict[str, Any]:
            revision = int(state.get("rtl_revision", 0))
            maximum = int(state.get("max_revisions", 0))
            evidence = {
                "rtl_revision": revision, "boundary": boundary,
                "failed_stage": failed_step.get("role") or failed_step.get("stage"),
                "run_id": failed_step.get("run_id"),
                "status": failed_step.get("status"),
                "collection": failed_step.get("collection"),
                "gate_failure": failed_step.get("gate_failure"),
            }
            state.setdefault("revision_history", []).append(evidence)
            if revision >= maximum:
                state.update({"status": "stopped", "boundary": boundary,
                              "stop_reason": "automatic_revision_budget_exhausted"})
                save()
                return {**state, "pipeline_id": checkpoint["pipeline_id"],
                        "revision_id": checkpoint["revision"],
                        "authority": "all pass/fail outcomes are Runtime-backed"}
            state.update({
                "status": "running", "boundary": None, "candidate_id": None,
                "verification_id": None, "rtl_revision": revision + 1,
                # Only the independent verification agent sees evaluator
                # evidence.  RTLScout receives the next frozen oracle and
                # remains unable to edit or approve it.
                "verification_feedback": json.dumps(evidence, ensure_ascii=False,
                                                    sort_keys=True)[:12000],
            })
            save()
            next_payload = {**payload,
                            "verification_feedback": state["verification_feedback"],
                            "max_revisions": maximum}
            return self.run_automated_rtl_pipeline(
                spec_id, next_payload, owner_id=owner_id,
                include_legacy=include_legacy)

        rtl_step = step("rtlscout")
        if rtl_step is None:
            submitted = self.submit_automated_rtlscout(
                spec_id, {**payload,
                          **({"verification_feedback": state["verification_feedback"]}
                             if state.get("verification_feedback") else {})},
                owner_id=owner_id, include_legacy=include_legacy)
            state["verification_id"] = submitted.get("verification_id")
            rtl_step = remember_submission("rtlscout", submitted)
        if not execute(rtl_step, "rtl_revision_required"):
            return revise_or_stop("rtl_revision_required", rtl_step)
        candidate_id = str(state.get("candidate_id") or "")
        if not candidate_id:
            raise RuntimeError("RTLScout collection did not pin a candidate_id")

        verify_step = step("compile_lint")
        if verify_step is None:
            verify_step = remember_submission("compile_lint", self.submit_rtl_verification(
                spec_id, owner_id=owner_id, include_legacy=include_legacy,
                candidate_id=candidate_id))
        if not execute(verify_step, "rtl_revision_required"):
            return revise_or_stop("rtl_revision_required", verify_step)

        sim_step = step("simulation")
        if sim_step is None:
            sim_step = remember_submission("simulation", self.submit_rtl_simulation(
                spec_id, {}, owner_id=owner_id, include_legacy=include_legacy,
                candidate_id=candidate_id))
        if not execute(sim_step, "rtl_revision_required"):
            return revise_or_stop("rtl_revision_required", sim_step)

        mutation_step = step("mutation_quality")
        if mutation_step is None:
            mutation_step = remember_submission("mutation_quality", self.submit_rtl_mutation_test(
                spec_id, {
                "verifier_identity": "verification-agent-v2/runtime-mutation",
                "maximum_mutants": payload.get("maximum_mutants", 32),
                "minimum_score": payload.get("minimum_mutation_score", .80),
                }, owner_id=owner_id, include_legacy=include_legacy,
                candidate_id=candidate_id))
        if not execute(mutation_step, "verification_revision_required",
                       require_collected_pass=True):
            return revise_or_stop("verification_revision_required", mutation_step)

        orfs_step = step("orfs_baseline")
        if orfs_step is None:
            try:
                promoted = self.promote_verified_rtl_to_orfs(
                    spec_id, owner_id=owner_id, include_legacy=include_legacy,
                    candidate_id=candidate_id)
            except ValueError as exc:
                promotion_failure = {"stage": stage_name("promotion_gate"),
                                       "role": "promotion_gate",
                                       "rtl_revision": int(state.get("rtl_revision", 0)),
                                       "status": "failed",
                                       "boundary": "verification_revision_required",
                                       "reason": str(exc)[:400]}
                state["steps"].append(promotion_failure)
                save()
                return revise_or_stop("verification_revision_required",
                                      promotion_failure)
            orfs_step = remember_submission("orfs_baseline", promoted)
        if execute_orfs:
            if not execute(orfs_step, "implementation_diagnosis_required"):
                return {**state, "pipeline_id": checkpoint["pipeline_id"],
                        "revision": checkpoint["revision"],
                        "authority": "all pass/fail outcomes are Runtime-backed"}
        state.update({"status": "baseline_succeeded" if execute_orfs else "baseline_submitted",
                      "boundary": None})
        save()
        return {**state, "pipeline_id": checkpoint["pipeline_id"],
                "revision": checkpoint["revision"], "resumed": False,
                "authority": "all pass/fail outcomes are Runtime-backed"}

    def auto_reflect_hypothesis(self, payload: dict[str, Any], *, owner_id: str | None = None,
                                include_legacy: bool = False) -> dict[str, Any]:
        """Use a reflection agent to turn *measured* runs into a testable claim.

        The model receives EDAIR packets, never a shell or action surface.  Its
        output is persisted as a draft hypothesis only; a controlled run and a
        held-out design are still required before it can influence planning.
        """
        run_ids = payload.get("run_ids")
        if not isinstance(run_ids, list) or not 1 <= len(run_ids) <= 8 or not all(isinstance(x, str) for x in run_ids):
            raise ValueError("run_ids must contain one to eight Runtime run identifiers")
        packets, refs = [], []
        for run_id in run_ids:
            result = self.runtime_edair(run_id, owner_id=owner_id,
                                        include_legacy=include_legacy, focus="diagnosis")
            packet = result["evidence_packet"]
            packets.append(packet)
            refs.append({"ref": f"edair:{run_id}", "sha256": packet["edair_fingerprint"]})
        trace = self.agent_traces.create("因果反思", "causal-reflection-agent")
        trace.add("goal", "从可追溯 EDA 证据提出可证伪假设", detail="假设不是结论，不能直接执行")
        step = trace.start_tool("codex-cli", "从 EDAIR 取证包提取机制、反例与受限干预")
        started = time.time()
        try:
            reflection = _codex_causal_reflection(packets)
            record = reflection_hypothesis(
                claim=reflection["claim"], mechanism=reflection["mechanism"],
                context={"run_ids": run_ids, "edair_fingerprints": [x["edair_fingerprint"] for x in packets],
                         "uncertainty": reflection["uncertainty"]}, evidence_refs=refs,
                producer="causal-reflection-agent-v2/codex-cli",
                proposed_intervention=reflection["proposed_intervention"],
            )
            event_id = self.hypothesis_ledger.append(record)
            step.duration_ms = int((time.time() - started) * 1000)
            trace.finish_tool(step, ok=True, detail="已产生可证伪草案；尚无跨设计结论")
            trace.add("evaluate", "因果边界", status="ok",
                      detail="只能通过预注册局部干预与留出设计复验升级")
            trace.status = "done"; trace.result = {"hypothesis_id": record["hypothesis_id"], "event_id": event_id}
            self.agent_traces.save(trace)
            return {"hypothesis": record, "event_id": event_id, "agent_trace_id": trace.trace_id,
                    "next": "run a pre-registered controlled intervention; do not execute a repair from this draft",
                    "execution_allowed": False}
        except Exception as exc:
            step.duration_ms = int((time.time() - started) * 1000)
            trace.finish_tool(step, ok=False, detail=str(exc)[:400])
            trace.status = "failed"; trace.result = {"error": str(exc)[:400]}; self.agent_traces.save(trace)
            raise

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

    def _evidence_rag_for_owner(self, owner_id: str | None) -> EvidenceRAG:
        """Return an owner-partitioned RAG without using the owner as a path."""
        partition = hashlib.sha256((owner_id or "legacy-local").encode("utf-8")).hexdigest()
        return EvidenceRAG(self.evidence_rag_root / f"{partition}.db")


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


    @staticmethod
    def _v2_objectives(profile: str):
        """Learning-schema objectives behind the visible QoR preference."""
        from openroad_platform_contracts import ObjectiveSpec
        profiles = {
            "area": (ObjectiveSpec("area_um2", "min", 1.0),),
            "timing": (ObjectiveSpec("setup_wns_ns", "max", 1.0),),
            "power": (ObjectiveSpec("power_W", "min", 1.0),),
            "performance": (ObjectiveSpec("setup_wns_ns", "max", 1.0),),
            "balanced": (ObjectiveSpec("setup_wns_ns", "max", .40),
                         ObjectiveSpec("area_um2", "min", .35),
                         ObjectiveSpec("power_W", "min", .25)),
        }
        if profile not in profiles:
            raise ValueError("objective_profile must be balanced, area, timing, performance, or power")
        return profiles[profile]

    def start_bayesian_closed_loop(self, payload: dict[str, Any], *,
                                   owner_id: str | None = None,
                                   include_legacy: bool = False) -> dict[str, Any]:
        """Create a durable, multi-parameter BO/GP experiment with R replicas."""
        from openroad_platform_contracts import ParameterSpec
        design_id = str(payload.get("design_id") or "")
        design = self._owned_design(design_id, owner_id, include_legacy=include_legacy)
        profile = str(payload.get("objective_profile") or "balanced")
        objectives = self._v2_objectives(profile)
        requested_space = payload.get("parameter_space") or {
            "core_utilization_pct": [5.0, 80.0],
            "place_density": [.30, .80],
        }
        if not isinstance(requested_space, dict) or not 1 <= len(requested_space) <= 8:
            raise ValueError("parameter_space must define one to eight bounded parameters")
        allowed = {"core_utilization_pct": (1.0, 99.0),
                   "place_density": (.01, 1.0)}
        parameters = []
        for name, bounds in requested_space.items():
            if name not in allowed or not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
                raise ValueError(f"unsupported or malformed BO parameter: {name}")
            low, high = float(bounds[0]), float(bounds[1]); policy_low, policy_high = allowed[name]
            if not policy_low <= low < high <= policy_high:
                raise ValueError(f"BO bounds for {name} are outside platform policy")
            parameters.append(ParameterSpec(str(name), low, high))
        repetitions = int(payload.get("repetitions") or 3)
        rounds = int(payload.get("max_rounds") or 12)
        stall_window = int(payload.get("stall_window") or 3)
        if not 2 <= repetitions <= 8 or not 1 <= rounds <= 20 or stall_window != 3:
            raise ValueError("v2 requires 2-8 repetitions, 1-20 rounds, and a fixed 3-round stall window")
        if repetitions * (rounds + 1) > 64:
            raise ValueError("baseline plus all repeated BO rounds must fit the 64-run study budget")
        optimizer_seed = int(payload.get("optimizer_seed") or 20260824)
        if not 0 <= optimizer_seed <= 2_147_483_647:
            raise ValueError("optimizer_seed must be between 0 and 2147483647")
        default_replica_seeds = (101, 211, 307, 401, 503, 601, 701, 809)
        replica_or_seeds = [int(item) for item in (
            payload.get("replica_or_seeds") or default_replica_seeds[:repetitions]
        )]
        if (len(replica_or_seeds) != repetitions
                or len(set(replica_or_seeds)) != repetitions
                or any(not 0 <= item <= 2_147_483_647 for item in replica_or_seeds)):
            raise ValueError(
                "replica_or_seeds must contain one distinct OpenROAD seed per repetition"
            )
        minimum_improvement = float(payload.get("minimum_relative_improvement") or .005)
        if not 0 <= minimum_improvement <= .25:
            raise ValueError("minimum_relative_improvement must be between 0 and 0.25")
        platform = str(payload.get("platform") or "nangate45")
        stage_timeout_seconds = int(payload.get("stage_timeout_seconds") or 3600)
        flow_timeout_seconds = int(payload.get("flow_timeout_seconds") or 7200)
        if not 60 <= stage_timeout_seconds <= 14_400:
            raise ValueError("stage_timeout_seconds must be between 60 and 14400")
        if not stage_timeout_seconds <= flow_timeout_seconds <= 28_800:
            raise ValueError(
                "flow_timeout_seconds must cover one stage and be at most 28800"
            )
        base = build_orfs_task(
            self.designs.rtl_path(design_id, owner_id=owner_id, include_legacy=include_legacy),
            project_id="openroad-platform", design_id=design_id, top=design["module"],
            clock=_optional_string(payload.get("clock")), platform_name=platform,
            target_stage=str(payload.get("target_stage") or "finish"),
            clock_period_ns=float(payload.get("clock_period_ns") or 10),
            core_utilization_pct=float(payload.get("core_utilization_pct") or 30),
            place_density=float(payload.get("place_density") or .55),
            or_seed=replica_or_seeds[0],
            stage_timeout_seconds=stage_timeout_seconds,
            timeout_seconds=flow_timeout_seconds,
            labels={"v2_closed_loop": "baseline", **({"owner_id": owner_id} if owner_id else {})},
        )
        hard_constraints = list(payload.get("hard_constraints") or [
            {"metric": "setup_wns_ns", "operator": ">=", "threshold": 0.0},
            {"metric": "drc_errors", "operator": "<=", "threshold": 0.0},
        ])
        subject = str(payload.get("experiment_key") or f"{design_id}-{uuid.uuid4().hex}")
        initial = {
            "status": "baseline_running", "design_id": design_id, "profile": profile,
            "base_task": base.to_dict(), "parameter_space": [item.to_dict() for item in parameters],
            "objectives": [item.to_dict() for item in objectives],
            "hard_constraints": hard_constraints, "repetitions": repetitions,
            "optimizer_seed": optimizer_seed,
            "replica_or_seeds": replica_or_seeds,
            "max_rounds": rounds, "stall_window": 3,
            "minimum_relative_improvement": minimum_improvement,
            "max_parallel": max(1, min(int(payload.get("max_parallel") or repetitions), 16)),
            "round": 0, "stalled_rounds": 0, "best_utility": -1.0,
            "best_feasible": False,
            "best_round": 0, "study_id": None, "history": [], "diagnosis": None,
            "active_kind": "baseline", "active_parameters": {
                item.name: base.parameters[item.name] for item in parameters
            },
            "active_proposal_id": None, "active_run_ids": [],
            "agent_events": [
                {"phase": "map", "claim": "bound design, platform, Runtime and run budget",
                 "execution_allowed": False},
                {"phase": "semantic", "claim": "translated QoR preference into explicit objective weights and hard constraints",
                 "objective_profile": profile, "execution_allowed": False},
                {"phase": "experiment", "claim": "pre-registered repeated baseline and three-stall stopping rule",
                 "repetitions": repetitions, "stall_window": 3,
                 "minimum_relative_improvement": minimum_improvement,
                 "execution_allowed": False},
            ],
        }
        checkpoint = self.pipeline_checkpoints.create_or_get(
            pipeline_kind="bo-gp-closed-loop-v2", subject_id=subject,
            owner_id=owner_id, initial_state=initial,
        )
        state = checkpoint["state"]
        if len(state["active_run_ids"]) > state["repetitions"]:
            raise RuntimeError("closed-loop checkpoint contains too many baseline replicas")
        if len(state["active_run_ids"]) < state["repetitions"]:
            for replica in range(len(state["active_run_ids"]), state["repetitions"]):
                task = TaskSpec.from_dict({**state["base_task"],
                    "task_id": f"{checkpoint['pipeline_id']}-baseline-r{replica}",
                    "parameters": {**state["base_task"]["parameters"],
                                   "or_seed": state["replica_or_seeds"][replica]},
                    "labels": {**state["base_task"].get("labels", {}),
                               "v2_pipeline_id": checkpoint["pipeline_id"],
                               "v2_round": "baseline", "replica_index": str(replica),
                               "or_seed": str(state["replica_or_seeds"][replica])}})
                state["active_run_ids"].append(self.runtime.submit(task).run_id)
                # Commit every child identity separately.  A crash can then
                # resume from the first missing replica without orphaning or
                # duplicating already submitted Runtime work.
                checkpoint = self.pipeline_checkpoints.save(
                    checkpoint["pipeline_id"], state,
                    expected_revision=checkpoint["revision"])
        if owner_id:
            self.auth.bind_resource("v2_closed_loop", checkpoint["pipeline_id"], owner_id)
        return {**checkpoint, "execution_started": True,
                "authority": "BO proposes combinations; Runtime/OpenROAD measures every replica"}

    def run_bayesian_closed_loop_to_boundary(self, pipeline_id: str,
                                              payload: dict[str, Any], *,
                                              owner_id: str | None = None,
                                              include_legacy: bool = False) -> dict[str, Any]:
        """Resume the BO/GP loop until completion or the 3-round diagnosis boundary."""
        from openroad_platform_contracts import ObjectiveSpec, OptimizationStudy, ParameterSpec
        checkpoint = self.pipeline_checkpoints.get(pipeline_id)
        if checkpoint["pipeline_kind"] != "bo-gp-closed-loop-v2":
            raise KeyError(pipeline_id)
        if owner_id and checkpoint.get("owner_id") not in {None, owner_id} and not include_legacy:
            raise KeyError(pipeline_id)
        state = checkpoint["state"]
        objectives = tuple(ObjectiveSpec.from_dict(item) for item in state["objectives"])
        parameters = tuple(ParameterSpec.from_dict(item) for item in state["parameter_space"])
        exporter = RuntimeEvidenceExporter(self.runtime_store)
        transitions = max(1, min(int(payload.get("max_transitions") or 64), 256))

        def save() -> None:
            nonlocal checkpoint
            checkpoint = self.pipeline_checkpoints.save(
                pipeline_id, state, expected_revision=checkpoint["revision"])

        for _ in range(transitions):
            if state["status"] in {"completed", "diagnosis_required", "failed"}:
                break
            run_ids = list(state.get("active_run_ids") or [])
            if len(run_ids) > state["repetitions"]:
                state.update({"status": "failed", "diagnosis": {
                    "reason": "checkpoint contains too many active replicas"}})
                save(); break
            # Complete a partially submitted round after process interruption.
            # Task IDs and the checkpointed list make the missing suffix
            # deterministic.
            if len(run_ids) < state["repetitions"]:
                for replica in range(len(run_ids), state["repetitions"]):
                    task = TaskSpec.from_dict({**state["base_task"],
                        "task_id": (f"{pipeline_id}-baseline-r{replica}"
                                    if state["active_kind"] == "baseline"
                                    else f"{pipeline_id}-round-{state['round']}-r{replica}"),
                        "parameters": {**state["base_task"]["parameters"],
                                       **state["active_parameters"],
                                       "or_seed": state["replica_or_seeds"][replica]},
                        "labels": {**state["base_task"].get("labels", {}),
                                   "v2_pipeline_id": pipeline_id,
                                   "v2_round": ("baseline" if state["active_kind"] == "baseline"
                                                else str(state["round"])),
                                   **({"optimizer_proposal_id": state["active_proposal_id"]}
                                      if state.get("active_proposal_id") else {}),
                                   "replica_index": str(replica),
                                   "or_seed": str(state["replica_or_seeds"][replica])}})
                    state["active_run_ids"].append(self.runtime.submit(task).run_id)
                    save()
                run_ids = list(state["active_run_ids"])
            with ThreadPoolExecutor(max_workers=min(state["max_parallel"], len(run_ids) or 1)) as pool:
                futures = []
                for run_id in run_ids:
                    run = self.runtime_store.get_run(run_id)
                    if run.status.value not in {"succeeded", "failed", "cancelled", "timed_out"}:
                        futures.append(pool.submit(self.runtime.execute_once, run_id))
                for future in futures:
                    future.result()
            if not run_ids:
                state.update({"status": "failed", "diagnosis": {"reason": "no active Runtime replicas"}})
                save(); break
            context = self._learning_context_for_run(self.runtime_store.get_run(run_ids[0]))
            if not state.get("study_id"):
                study = OptimizationStudy(
                    study_id=f"study-{uuid.uuid4().hex[:20]}", design_id=state["design_id"],
                    context_fingerprint=context.fingerprint, parameter_space=parameters,
                    objectives=objectives,
                    max_runs=min(64, state["repetitions"] * (state["max_rounds"] + 1)),
                    seed=int(state["optimizer_seed"]), status="active",
                )
                state["study_id"] = self.optimization_store.create(study)
                historical = []
                try:
                    candidates = self.tenant_learning_store.list(
                        owner_id or "system-auto", "openroad-platform")
                except ValueError:
                    candidates = []
                for item in reversed(candidates):
                    if item.context.fingerprint != context.fingerprint:
                        continue
                    if not all(spec.lower <= float(item.parameters.get(spec.name, float("inf"))) <= spec.upper
                               for spec in parameters):
                        continue
                    if not all(obj.metric_name in item.metrics for obj in objectives):
                        continue
                    historical.append(item)
                    if len(historical) >= 24:
                        break
                state["memory_prior_observations"] = [
                    item.to_dict() for item in reversed(historical)]
                state["memory_prior_refs"] = [
                    {"observation_id": item.observation_id,
                     "fingerprint": item.fingerprint,
                     "run_id": item.run_id}
                    for item in reversed(historical)]
                knowledge_bundle = self._evidence_rag_for_owner(owner_id).retrieve(
                    "validated parameter interaction timing area power QoR",
                    context, limit=8, action_eligible_only=True)
                state["validated_knowledge_bundle"] = knowledge_bundle.to_dict()
                state["agent_events"].append({
                    "phase": "memory", "round": 0,
                    "claim": "retrieved context-exact numeric priors and validated semantic rules for BO warm start",
                    "prior_count": len(historical),
                    "observation_refs": state["memory_prior_refs"],
                    "knowledge_bundle_fingerprint": knowledge_bundle.bundle_fingerprint,
                    "validated_rule_count": len(knowledge_bundle.records),
                    "execution_allowed": False,
                })
                save()
            study = self.optimization_store.get(state["study_id"])
            observations = []
            for run_id in run_ids:
                observation = exporter.export_run(run_id, context)
                self.optimization_store.add_observation(study.study_id, observation)
                observations.append(observation)
                self.auto_collect_terminal_run(run_id)
            summary = summarize_replicates(observations, objectives, state["hard_constraints"])
            state["agent_events"].append({
                "phase": "validate", "round": state["round"],
                "claim": "aggregated repeated Runtime observations; predictions were excluded",
                "run_ids": list(run_ids), "eligible": summary["eligible"],
                "failure_rate": summary["failure_rate"], "execution_allowed": False,
            })
            if state["active_kind"] == "baseline":
                state["baseline_summary"] = summary
                state["best_feasible"] = bool(summary["eligible"])
                state["best_utility"] = 0.0 if summary["eligible"] else -1.0
                state["history"].append({"round": 0, "kind": "baseline",
                                         "parameters": state["active_parameters"],
                                         "summary": summary,
                                         "utility": 0.0 if summary["eligible"] else None,
                                         "decision": "baseline"})
                state["agent_events"].append({
                    "phase": "review", "round": 0,
                    "claim": "baseline replication passed admission gates"
                             if summary["eligible"] else "baseline evidence failed admission gates",
                    "execution_allowed": False,
                })
                if summary["successes"] != summary["replicas"] or not summary["complete_objectives"]:
                    state.update({"status": "failed", "diagnosis": {
                        "reason": "baseline failed replication or hard constraints",
                        "summary": summary}})
                    save(); break
            else:
                utility = relative_utility(summary, state["baseline_summary"], objectives)
                decision = stalled_decision(
                    candidate_utility=utility, best_utility=float(state["best_utility"]),
                    minimum_relative_improvement=float(state["minimum_relative_improvement"]),
                    stalled_rounds=int(state["stalled_rounds"]),
                    has_feasible_incumbent=bool(state.get(
                        "best_feasible", state.get("baseline_summary", {}).get("eligible"))),
                )
                state["history"].append({"round": state["round"], "kind": "bo_candidate",
                                         "parameters": state["active_parameters"],
                                         "summary": summary, "utility": utility,
                                         "decision": decision})
                state["agent_events"].extend([
                    {"phase": "review", "round": state["round"],
                     "claim": decision["reason"], "promoted": decision["promoted"],
                     "utility": utility, "execution_allowed": False},
                    {"phase": "memory", "round": state["round"],
                     "claim": "stored positive or negative replicated outcome with Runtime evidence",
                     "run_ids": list(run_ids), "outcome": (
                         "improved" if decision["promoted"] else "no_improvement"),
                     "execution_allowed": False},
                ])
                state["stalled_rounds"] = decision["stalled_rounds"]
                if decision["promoted"]:
                    state["best_utility"] = utility
                    state["best_round"] = state["round"]
                    state["best_feasible"] = True
                if state["stalled_rounds"] >= 3:
                    packet = diagnosis_packet(state["history"], objectives)
                    evidence_packets = []
                    for run_id in run_ids[:3]:
                        try:
                            evidence_packets.append(self.runtime_edair(
                                run_id, owner_id=owner_id, include_legacy=include_legacy,
                                focus="diagnosis")["evidence_packet"])
                        except (KeyError, ValueError):
                            continue
                    packet["evidence_packets"] = evidence_packets
                    parameter_names = [item.name for item in parameters]
                    if len(parameter_names) >= 2:
                        first, second = parameter_names[:2]
                        bounds = {item.name: [item.lower, item.upper] for item in parameters}
                        evidence_refs = []
                        causal_observations = self.optimization_store.observations(
                            study.study_id)
                        for observation in causal_observations[
                                -min(12, len(causal_observations)):]:
                            for pointer in observation.evidence:
                                if pointer.ref.startswith("run:"):
                                    evidence_refs.append(pointer.to_dict())
                                    break
                        if evidence_refs:
                            hypothesis = reflection_hypothesis(
                                claim=(f"The stalled QoR response may depend on an interaction "
                                       f"between {first} and {second}, not either parameter alone."),
                                mechanism=("Physical-design parameters jointly change available "
                                           "placement/routing freedom; a marginal one-parameter "
                                           "effect can therefore reverse under another setting."),
                                context={
                                    "pipeline_id": pipeline_id,
                                    "study_id": study.study_id,
                                    "design_id": state["design_id"],
                                    "context_fingerprint": study.context_fingerprint,
                                    "status": "three_round_stall",
                                },
                                evidence_refs=evidence_refs,
                                producer="closed-loop-diagnosis-v2",
                                proposed_intervention={
                                    "kind": "preregistered_2x2_interaction",
                                    "parameters": [first, second],
                                    "levels": {first: bounds[first],
                                               second: bounds[second]},
                                    "repetitions": state["repetitions"],
                                    "randomized_order": True,
                                    "execution_allowed": False,
                                },
                            )
                            event_id = self.hypothesis_ledger.append(hypothesis)
                            packet["causal_hypothesis"] = {
                                **hypothesis, "ledger_event_id": event_id}
                    state["agent_events"].append({
                        "phase": "diagnosis", "round": state["round"],
                        "claim": "three consecutive rounds missed the pre-registered improvement threshold",
                        "next": "repair_agent_stage_localization",
                        **({"hypothesis_id": packet["causal_hypothesis"]["hypothesis_id"]}
                           if packet.get("causal_hypothesis") else {}),
                        "execution_allowed": False,
                    })
                    state.update({"status": "diagnosis_required", "diagnosis": packet,
                                  "active_run_ids": []})
                    save(); break
            if state["round"] >= state["max_rounds"]:
                if state.get("best_feasible"):
                    state.update({"status": "completed", "active_run_ids": []})
                else:
                    packet = diagnosis_packet(state["history"], objectives)
                    packet.update({
                        "reason": "no hard-constraint-feasible baseline or candidate",
                        "next": "repair_agent_stage_localization",
                    })
                    state["agent_events"].append({
                        "phase": "diagnosis", "round": state["round"],
                        "claim": "the search budget ended without a hard-constraint-feasible vector",
                        "next": "repair_agent_stage_localization", "execution_allowed": False,
                    })
                    state.update({"status": "diagnosis_required", "diagnosis": packet,
                                  "active_run_ids": []})
                save(); break
            observations_all = self.optimization_store.observations(study.study_id)
            memory_priors = [
                LearningObservation.from_dict(item)
                for item in state.get("memory_prior_observations", [])]
            proposal = MultiObjectiveBayesianOptimizer(pool_size=512, exploration=.05).propose(
                study, observations_all, historical_observations=memory_priors)
            self.optimization_store.save_proposal(proposal)
            state["round"] += 1
            state["active_kind"] = "bo_candidate"
            state["active_parameters"] = proposal.parameters
            state["active_proposal_id"] = proposal.proposal_id
            state["active_run_ids"] = []
            state["status"] = "round_running"
            state["agent_events"].extend([
                {"phase": "hypothesis", "round": state["round"],
                 "claim": "the BO/GP coupled parameter vector may improve weighted QoR",
                 "proposal_id": proposal.proposal_id,
                 "parameters": proposal.parameters,
                 "execution_allowed": False},
                {"phase": "implement", "round": state["round"],
                 "claim": "submitted the allowlisted parameter intervention to Runtime",
                 "proposal_id": proposal.proposal_id,
                 "parameters": proposal.parameters,
                 "evidence_refs": [item.to_dict() for item in proposal.evidence],
                 "knowledge_bundle_fingerprint": (
                     state.get("validated_knowledge_bundle") or {}).get(
                         "bundle_fingerprint"),
                 "execution_allowed": True,
                 "authority": "only the declared parameter vector may be submitted to Runtime"},
            ])
            # Persist the round identity before its first child is submitted.
            save()
            for replica in range(state["repetitions"]):
                task = TaskSpec.from_dict({**state["base_task"],
                    "task_id": f"{pipeline_id}-round-{state['round']}-r{replica}",
                    "parameters": {**state["base_task"]["parameters"], **proposal.parameters,
                                   "or_seed": state["replica_or_seeds"][replica]},
                    "labels": {**state["base_task"].get("labels", {}),
                               "v2_pipeline_id": pipeline_id,
                               "v2_round": str(state["round"]),
                               "optimizer_proposal_id": proposal.proposal_id,
                               "replica_index": str(replica),
                               "or_seed": str(state["replica_or_seeds"][replica])}})
                state["active_run_ids"].append(self.runtime.submit(task).run_id)
                save()
        return {**checkpoint, "state": state,
                "run_to_boundary": {"transitions_budget": transitions,
                                    "stopped_at": state["status"]},
                "authority": "observed repeated Runtime evidence; predictions are not QoR"}


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
        teacher = teacher_context_from_holdout(
            source, holdout, validation, first=first, second=second, metric=metric)
        result = {"source": source, "holdout": holdout, "validation": validation,
                  "teacher_context": teacher, "knowledge_card": None}
        hypothesis_id = _optional_string(payload.get("hypothesis_id"))
        if hypothesis_id:
            history = self.hypothesis_ledger.history(hypothesis_id)
            if not history:
                raise KeyError(hypothesis_id)
            hypothesis = history[0]["record"]
            assessment = assess_hypothesis(
                hypothesis, intervention_report=source,
                expected_direction=str(payload.get("expected_direction") or "min"))
            self.hypothesis_ledger.append({**hypothesis, **assessment})
            promotion = promote_after_holdout(assessment, validation)
            rejected_transfer = (validation.get("eligible") is True
                                 and validation.get("outcome") == "rejected")
            previously_validated = any(
                item.get("record", {}).get("status") == "validated" for item in history
            )
            terminal_status = (
                "validated" if promotion["promoted"] else
                "retired" if rejected_transfer and previously_validated else
                "refuted" if rejected_transfer else assessment["status"]
            )
            terminal = {**hypothesis, **assessment,
                        "status": terminal_status,
                        "holdout_validation": validation,
                        "promotion": promotion}
            event_id = self.hypothesis_ledger.append(terminal)
            card = {
                "schema_version": 1, "kind": "causal_knowledge_card",
                "hypothesis_id": hypothesis_id, "status": terminal["status"],
                "claim": hypothesis["claim"], "mechanism": hypothesis["mechanism"],
                "compound_condition": teacher.get("compound_condition"),
                "source_design_fingerprint": source.get("design_fingerprint"),
                "holdout_design_fingerprint": holdout.get("design_fingerprint"),
                "context_fingerprint": source.get("transfer_context_fingerprint"),
                "source_run_ids": source_ids, "holdout_run_ids": holdout_ids,
                "scope": promotion.get("scope", "source experiment only"),
                "ledger_event_id": event_id,
                "action_eligible": bool(promotion["promoted"]),
                "execution_allowed": False,
            }
            result.update({"assessment": assessment, "promotion": promotion,
                           "knowledge_card": card})
            digest = _sha256_text(json.dumps(card, sort_keys=True))
            rag = self._evidence_rag_for_owner(owner_id)
            if promotion["promoted"]:
                run = self.runtime_store.get_run(source_ids[0])
                context = self._learning_context_for_run(run)
                record = EvidenceKnowledgeRecordV2(
                    claim=(f"Validated compound condition for {first} × {second} "
                           f"on the two cited designs: {hypothesis['claim']}"),
                    knowledge_type="validated_rule", context=context,
                    evidence=EvidencePointer(
                        ref=f"source:causal-card:{hypothesis_id}", sha256=digest),
                    verified=True, scope="exact_design",
                    tags=("causal", "holdout", "interaction", first, second, metric),
                )
                try:
                    card["rag_record_id"] = rag.add(record)
                except sqlite3.IntegrityError:
                    card["rag_record_id"] = "existing-identical-card"
            elif rejected_transfer:
                card["retired_rag_record_ids"] = list(rag.revoke_by_evidence_ref(
                    f"source:causal-card:{hypothesis_id}",
                    reason="controlled held-out design contradicted the transferable interaction",
                    evidence_sha256=digest,
                ))
        return result

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
                      include_legacy: bool = False, focus: str | None = None) -> dict[str, Any]:
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
        timing = None
        timing_candidates = [
            item for item in artifacts
            if item.get("kind") in {"report", "log"}
            and re.search(r"(timing|sta|setup|hold|6_finish\.rpt)",
                          str(item.get("store_key") or ""), re.I)]
        for timing_artifact in timing_candidates:
            attempt = next((a for s in view.get("stages", []) for a in s.get("attempts", [])
                            if any(x.get("artifact_id") == timing_artifact["artifact_id"]
                                   for x in a.get("artifacts", []))), None)
            path = Path(attempt["workspace"]) / timing_artifact["store_key"] if attempt else None
            if not path or not path.is_file() or _sha256(path) != timing_artifact["sha256"]:
                continue
            parsed_timing = parse_opensta_paths(path)
            if not parsed_timing["paths"]:
                continue
            timing_source = {
                "artifact_id": timing_artifact["artifact_id"],
                "sha256": timing_artifact["sha256"], "kind": timing_artifact["kind"],
                "parser": parsed_timing["parser"],
                "parser_version": parsed_timing["parser_version"],
                "source_size_bytes": timing_artifact.get("size_bytes"),
            }
            timing = timing_ir(
                parsed_timing["paths"], source=timing_source,
                truncated=parsed_timing["truncated"])
            timing["parser_fidelity"] = {
                "total_startpoint_blocks": parsed_timing["total_startpoint_blocks"],
                "parsed_paths": len(parsed_timing["paths"]),
                "unparsed_blocks": parsed_timing["unparsed_blocks"],
            }
            break
        physical = None
        physical_instances: list[dict[str, Any]] = []
        physical_nets: list[dict[str, Any]] = []
        physical_violations: list[dict[str, Any]] = []
        physical_source = None
        def_artifact = next((item for item in artifacts if item.get("kind") == "def"), None)
        if def_artifact:
            attempt = next((a for s in view.get("stages", []) for a in s.get("attempts", [])
                            if any(x.get("artifact_id") == def_artifact["artifact_id"]
                                   for x in a.get("artifacts", []))), None)
            path = Path(attempt["workspace"]) / def_artifact["store_key"] if attempt else None
            if path and path.is_file() and _sha256(path) == def_artifact["sha256"]:
                cells, die = read_def(path)
                physical_source = {
                    "artifact_id": def_artifact["artifact_id"],
                    "sha256": def_artifact["sha256"], "kind": "def",
                    "parser": "openroad-def-placement",
                    "parser_version": "cell-coords-v1",
                    "source_size_bytes": def_artifact.get("size_bytes"),
                }
                physical_instances = [
                    {"name": item["name"], "cell_type": item["type"],
                     "x": item["x1"], "y": item["y1"],
                     "width": None, "height": None, "orientation": None,
                     "evidence": physical_source}
                    for item in cells]
        if design_ir is not None and netlist is not None:
            net_source = {
                "artifact_id": netlist["artifact_id"], "sha256": netlist["sha256"],
                "kind": "netlist", "parser": "verilog-netlist-connectivity",
                "parser_version": "v1", "source_size_bytes": netlist.get("size_bytes"),
            }
            if physical_source is None:
                physical_source = net_source
            endpoints: dict[str, set[str]] = {}
            for instance in design_ir.get("instances", []):
                for signal in (instance.get("named_connections") or {}).values():
                    name = str(signal).strip()
                    if name:
                        endpoints.setdefault(name, set()).add(str(instance["name"]))
            for port in design_ir.get("ports", []):
                name = str(port.get("name") or "").strip()
                if name:
                    endpoints.setdefault(name, set()).add(f"port:{name}")
            physical_nets = [
                {"name": name, "fanout": len(users), "wirelength_um": None,
                 "evidence": net_source}
                for name, users in sorted(endpoints.items())
            ]
        envelope = view.get("analysis_report") or {}
        report = envelope.get("report") if isinstance(envelope, dict) else None
        if isinstance(report, dict) and isinstance(envelope.get("source_sha256"), str):
            diagnosis = report.get("diagnosis") if isinstance(report.get("diagnosis"), dict) else {}
            rows = diagnosis.get("violations") if isinstance(diagnosis.get("violations"), list) else []
            source = {"artifact_id": str(envelope.get("source_artifact_id") or "analysis-report"),
                      "sha256": envelope["source_sha256"], "kind": "report",
                      "parser": "openroad-analysis-report", "parser_version": "v1",
                      "source_size_bytes": envelope.get("source_size_bytes")}
            physical_violations = [
                {"rule": str(row.get("type") or "reported_violation"),
                 "severity": row.get("severity"), "evidence": source}
                for row in rows if isinstance(row, dict)]
            if physical_source is None:
                physical_source = source
        if physical_source is not None:
            physical = physical_ir(
                instances=physical_instances, nets=physical_nets,
                violations=physical_violations, source=physical_source,
                grid={"available": bool(((report or {}).get("cell_density") or {}).get("available"))},
                truncated=len(physical_violations) > 256)
        edair = build_edair(design=design_ir, run=run_ir, timing=timing,
                            physical=physical, raw_artifacts=refs)
        return {"edair": edair, "agent_view": agent_evidence_view(edair),
                **({"evidence_packet": evidence_packet(edair, focus=focus)} if focus else {}),
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

    def runtime_artifact_excerpt(self, run_id: str, artifact_id: str, *,
                                 owner_id: str | None = None,
                                 include_legacy: bool = False,
                                 offset: int = 0, length: int = 16_384) -> dict[str, Any]:
        """Read a bounded, integrity-checked raw text range for an Agent.

        EDAIR summaries deliberately do not inline multi-megabyte reports,
        DEF, or logs.  This range interface lets a diagnosis agent recover the
        omitted detail without bypassing Runtime ownership or silently reading
        changed bytes.  Binary artifacts remain downloadable but are not
        decoded into an LLM prompt.
        """
        if offset < 0 or not 1 <= length <= 65_536:
            raise ValueError("excerpt requires offset >= 0 and length in [1, 65536]")
        path, content_type = self.runtime_artifact(
            run_id, artifact_id, owner_id=owner_id,
            include_legacy=include_legacy)
        data = path.read_bytes()
        if b"\x00" in data[:min(len(data), 8192)]:
            raise ValueError("binary artifacts do not support text excerpts")
        raw = data[offset:offset + length]
        try:
            content = raw.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            content = raw.decode("utf-8", errors="replace")
            encoding = "utf-8-with-replacement"
        return {
            "kind": "runtime_artifact_excerpt", "schema_version": 1,
            "run_id": run_id, "artifact_id": artifact_id,
            "source_sha256": _sha256(path), "source_size_bytes": len(data),
            "content_type": content_type, "offset": offset,
            "requested_length": length, "returned_bytes": len(raw),
            "end_offset": offset + len(raw), "eof": offset + len(raw) >= len(data),
            "excerpt_sha256": hashlib.sha256(raw).hexdigest(),
            "encoding": encoding, "content": content,
            "loss_manifest": {
                "bytes_before": min(offset, len(data)),
                "bytes_after": max(0, len(data) - offset - len(raw)),
            },
            "execution_allowed": False,
        }

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

    def _learning_context_for_run(self, run, *, metric_parser_version=None):
        """Build the learning context for a run (shared by manual + auto paths)."""
        rtl = run.task_spec.inputs.get("rtl")
        rtl_sha = rtl.get("sha256") if isinstance(rtl, dict) else run.task_spec.inputs.get("rtl_sha256")
        if not isinstance(rtl_sha, str):
            raise ValueError("Runtime task has no immutable RTL fingerprint")
        stages = self.runtime_store.list_stages(run.run_id)
        plugin_version = str(stages[0].plugin_version if stages else "registered")
        # BO may vary physical implementation knobs, but it must never learn
        # across a relaxed design specification.  In particular, a 20 ns run
        # is not prior evidence for the same RTL constrained at 10 ns.
        invariant_constraints = {
            "top": run.task_spec.inputs.get("top"),
            "clock": run.task_spec.inputs.get("clock"),
            "platform": run.task_spec.parameters.get("platform"),
            "target_stage": run.task_spec.parameters.get("target_stage"),
            "clock_period_ns": run.task_spec.parameters.get("clock_period_ns"),
            "minimum_die_size_um": run.task_spec.parameters.get("minimum_die_size_um"),
        }
        constraint_fingerprint = _sha256_text(json.dumps(
            invariant_constraints, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")))
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
            constraint_fingerprint=constraint_fingerprint,
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
                "oracle_provenance": run.task_spec.inputs.get("oracle_provenance", {}),
                # Keep normalized top-level fields as part of the stable
                # candidate contract.  Older code stored only the nested
                # adapter payload, which let generated-oracle mutation gates
                # miss automatic Verification-Agent candidates.
                "oracle_origin": str((run.task_spec.inputs.get("oracle_provenance") or {}).get("origin") or ""),
                "oracle_reviewed_by": str((run.task_spec.inputs.get("oracle_provenance") or {}).get("reviewed_by") or ""),
                "testbench_top": str((run.task_spec.inputs.get("oracle_provenance") or {}).get("testbench_top") or "")},
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
        try:
            self.rtl_frontend.add_check(
                check_id=f"rtlmutation-{run_id}", candidate_id=candidate_id,
                check_kind="mutation_quality", status=status,
                evidence_ref=ref, evidence_sha256=digest, detail=detail,
            )
        except ValueError as exc:
            if "already exists" not in str(exc):
                raise
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
        # A newly materialized SpecIR intentionally precedes design
        # registration: RTLScout must generate and verify RTL before a Design
        # record can exist.  Authorize that interval through the immutable
        # rtl_spec ownership binding; fall back to Design ownership only for
        # imported/legacy lineages.
        if owner_id and not self.auth.owns_resource(
            "rtl_spec", spec_id, owner_id, include_legacy=include_legacy
        ):
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

    @staticmethod
    def _pinned_rtl_candidate(lineage: dict[str, Any],
                              candidate_id: str | None) -> dict[str, Any]:
        candidates = lineage.get("candidates") or []
        if candidate_id is None:
            candidate = candidates[-1] if candidates else None
        else:
            candidate = next((item for item in candidates
                              if item.get("candidate_id") == candidate_id), None)
        if candidate is None:
            raise ValueError("SpecIR has no matching RTL candidate")
        return candidate

    def submit_rtl_verification(self, spec_id: str, *, owner_id: str | None = None,
                                include_legacy: bool = False,
                                candidate_id: str | None = None) -> dict[str, Any]:
        if not self.rtl_verify_readiness["ready"]:
            raise ValueError(self.rtl_verify_readiness["reason"])
        lineage = self.get_rtl_lineage(spec_id, owner_id=owner_id, include_legacy=include_legacy)
        candidate = self._pinned_rtl_candidate(lineage, candidate_id)
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
                              include_legacy: bool = False,
                              candidate_id: str | None = None) -> dict[str, Any]:
        if not self.rtl_sim_readiness["ready"]:
            raise ValueError(self.rtl_sim_readiness["reason"])
        lineage = self.get_rtl_lineage(spec_id, owner_id=owner_id, include_legacy=include_legacy)
        candidate = self._pinned_rtl_candidate(lineage, candidate_id)
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
                                 include_legacy: bool = False,
                                 candidate_id: str | None = None) -> dict[str, Any]:
        """Run mutations against an already frozen, separately reviewed oracle."""
        if not self.rtl_mutation_readiness["ready"]:
            raise ValueError(self.rtl_mutation_readiness["reason"])
        lineage = self.get_rtl_lineage(spec_id, owner_id=owner_id, include_legacy=include_legacy)
        candidate = self._pinned_rtl_candidate(lineage, candidate_id)
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
                                     include_legacy: bool = False,
                                     candidate_id: str | None = None) -> dict[str, Any]:
        """Submit ORFS only after compile and functional evidence are recorded.

        A lint/synthesis success is a structural gate, never a functional
        proof.  The functional gate is intentionally fail-closed until a
        frozen simulation, formal, or equivalence oracle has produced a
        Runtime-backed pass for this candidate.
        """
        lineage = self.get_rtl_lineage(spec_id, owner_id=owner_id, include_legacy=include_legacy)
        candidate = self._pinned_rtl_candidate(lineage, candidate_id)
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
        provenance = candidate.get("provenance", {})
        nested_oracle = provenance.get("oracle_provenance") or {}
        oracle_origin = str(provenance.get("oracle_origin") or nested_oracle.get("origin") or "")
        if oracle_origin in {"approved_generated", "independent_verifier_agent"}:
            mutation = [item for item in lineage["checks"]
                        if item["candidate_id"] == candidate["candidate_id"]
                        and item["check_kind"] == "mutation_quality" and item["status"] == "passed"]
            if not mutation:
                raise ValueError("A generated verification oracle requires a passing Runtime mutation-quality check before ORFS promotion")
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
            # Frontend SpecIR may say "synthesis" while describing how RTL is
            # to be produced.  That field cannot weaken the backend acceptance
            # target: v2 promotion means a complete physical baseline through
            # finish/GDS, not a standalone synthesis tutorial mode.
            target_stage="finish",
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

    def _spec_provider_from_payload(self, payload: dict[str, Any]):
        name = str(payload.get("provider") or "codex-cli")
        model = _optional_string(payload.get("model"))
        if name != "codex-cli":
            raise ValueError(
                "v2 internal mode uses only the platform-managed codex-cli provider"
            )
        if model is not None and model != "gpt-5.6-terra":
            raise ValueError("v2 uses the fixed platform Codex model: gpt-5.6-terra")
        return self._spec_provider(name, "gpt-5.6-terra")

    def _spec_provider_for_session(self, session_id: str, session: dict[str, Any]):
        if session["provider"] != "codex-cli":
            raise ValueError("This legacy Spec session must be recreated under v2 Codex-only mode")
        return self._spec_provider("codex-cli", "gpt-5.6-terra")

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
        if name in {"codex", "codex-cli"}:
            if model not in {None, "gpt-5.6-terra"}:
                raise ValueError("only gpt-5.6-terra is enabled")
            return CodexCliSpecProvider(model="gpt-5.6-terra")
        raise ValueError("only the platform-managed codex-cli provider is enabled")


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


def _validate_rtlscout_testbench(
    source: str,
    top: str,
    *,
    testbench_top: str | None = None,
    require_upstream_protocol: bool = False,
) -> None:
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
    if testbench_top is not None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", testbench_top):
            raise ValueError("RTLScout verification oracle requires a valid testbench_top")
        if not re.search(rf"\bmodule\s+{re.escape(testbench_top)}\b", source):
            raise ValueError("testbench_top does not match a declared testbench module")
    if require_upstream_protocol:
        if testbench_top != "tb":
            raise ValueError("RTLScout upstream evaluator requires testbench_top='tb'")
        if not re.search(r"TB_SUMMARY\s+total=", source):
            raise ValueError(
                "RTLScout upstream evaluator requires a TB_SUMMARY total=N errors=M marker"
            )
        if re.search(r"\b(?:break|continue)\s*;", source):
            raise ValueError(
                "independent simulation requires an Icarus-compatible testbench without break/continue"
            )


def _codex_testbench_draft(spec: SpecIR, *, feedback: str | None = None) -> dict[str, Any]:
    """Run the isolated verification-agent prompt against a frozen SpecIR.

    This helper only creates bytes and a structural result.  The automatic
    route content-addresses those bytes under the verification-agent identity;
    the preview route deliberately returns them without freezing anything.
    """
    executable = shutil.which("codex")
    if not executable:
        raise ValueError("Codex CLI is unavailable; provide an existing or reviewed generated oracle instead")
    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["testbench_source", "testbench_top", "assumptions", "coverage_plan", "open_questions"],
        "properties": {
            "testbench_source": {"type": "string", "maxLength": 200000},
            "testbench_top": {"type": "string", "const": "tb"},
            "assumptions": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
            "coverage_plan": {"type": "array", "items": {"type": "string"}, "maxItems": 40},
            "open_questions": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        },
    }
    prompt = (
        "Generate a SystemVerilog TESTBENCH DRAFT, not RTL, from the approved SpecIR below. "
        "It must declare exactly `module tb` and return testbench_top=`tb`, instantiate the exact DUT top module as instance dut, contain stimulus and self-checking "
        "failure paths ($fatal/error/assertion), maintain integer total_checks and total_errors counters, print exactly `TB_SUMMARY total=%0d errors=%0d` with those counters, then print PASS only when errors is zero, and finish. "
        "The same frozen testbench must compile under both Verilator 5.x and Icarus Verilog -g2012: use a conservative portable subset and never use break or continue statements. "
        "Never modify the DUT contract, never claim completeness, and list ambiguous behavior in open_questions. "
        "You are the independent verification agent, not the RTL author. "
        "Return assumptions and open questions honestly; never claim formal completeness. "
        "Return only JSON matching the supplied schema. Do not invoke tools.\n\n"
        f"SPECIR={json.dumps(spec.to_dict(), ensure_ascii=False, sort_keys=True)}"
        + (
            "\n\nPREVIOUS_EXTERNAL_EVALUATOR_FEEDBACK="
            + feedback[:12000]
            + "\nRevise test stimulus/checks to address this evidence. Do not change the DUT contract."
            if feedback else ""
        )
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
        _validate_rtlscout_testbench(
            source, spec.top, testbench_top=str(draft.get("testbench_top") or ""),
            require_upstream_protocol=True,
        )
        _compile_testbench_preflight(spec, source)
    except ValueError as exc:
        structural_error = str(exc)
    testbench_top = draft.get("testbench_top")
    if not isinstance(testbench_top, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", testbench_top):
        raise RuntimeError("Codex testbench draft has no valid testbench_top")
    if not re.search(rf"\bmodule\s+{re.escape(testbench_top)}\b", source):
        raise RuntimeError("Codex testbench_top does not match a declared module")
    return {
        "draft": {key: draft.get(key, []) for key in ("testbench_source", "testbench_top", "assumptions", "coverage_plan", "open_questions")},
        "draft_sha256": _sha256_text(source),
        "structural_floor_passed": structural_error is None,
        "structural_floor_error": structural_error,
        "authority": "verification-agent output preview; only the automatic dual-agent route may freeze it as an oracle",
        "execution_allowed": False,
    }


def _compile_testbench_preflight(spec: SpecIR, testbench: str) -> None:
    """Compile a generated oracle against an interface-identical stub DUT.

    This does not execute stimulus or claim functional correctness.  It closes
    a narrower but essential boundary: a syntactically invalid oracle must be
    rejected before RTLScout spends its candidate budget against it.
    """
    verilator = ROOT / ".tools" / "verilator-5.040" / "bin" / "verilator"
    if not verilator.is_file():
        discovered = shutil.which("verilator")
        if not discovered:
            raise ValueError("verification preflight requires Verilator")
        verilator = Path(discovered)
    iverilog = Path(os.environ.get(
        "OPENROAD_PLATFORM_RTL_TOOLS_ROOT",
        "/share/home/yuanwenjie/.local/opt/openroad-rtl-tools",
    )).expanduser() / "bin" / "iverilog"
    if not iverilog.is_file():
        discovered = shutil.which("iverilog")
        if not discovered:
            raise ValueError("verification preflight requires Icarus Verilog")
        iverilog = Path(discovered)

    declarations, assignments = [], []
    for port in spec.ports:
        port_width = int(port.width or 1)
        width = "" if port_width == 1 else f"[{port_width - 1}:0] "
        direction = str(port.direction)
        if direction == "input":
            declarations.append(f"input wire {width}{port.name}")
        elif direction == "output":
            declarations.append(f"output wire {width}{port.name}")
            assignments.append(f"  assign {port.name} = '0;")
        else:
            declarations.append(f"inout wire {width}{port.name}")
    stub = (
        f"module {spec.top}(\n  " + ",\n  ".join(declarations) + "\n);\n"
        + "\n".join(assignments) + "\nendmodule\n"
    )
    with tempfile.TemporaryDirectory(prefix="openroad-tb-preflight-") as raw:
        root = Path(raw)
        stub_path, tb_path = root / "dut_stub.sv", root / "tb.sv"
        stub_path.write_text(stub, encoding="utf-8")
        tb_path.write_text(testbench, encoding="utf-8")
        commands = (
            [str(verilator), "--lint-only", "--sv", "--timing", "--Wno-fatal",
             "--top-module", "tb",
             str(stub_path), str(tb_path)],
            [str(iverilog), "-g2012", "-s", "tb", "-o", str(root / "simv"),
             str(stub_path), str(tb_path)],
        )
        for tool, command in (("Verilator", commands[0]), ("Icarus", commands[1])):
            completed = subprocess.run(
                command, cwd=root, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, timeout=60, check=False,
            )
            if completed.returncode != 0:
                detail = "\n".join((completed.stdout or "").splitlines()[-20:])
                raise ValueError(f"{tool} rejected the generated testbench:\n{detail}")


def _codex_causal_reflection(packets: list[dict[str, Any]]) -> dict[str, Any]:
    """Obtain a bounded causal *proposal* from provenance-bearing EDAIR packets."""
    executable = shutil.which("codex")
    if not executable:
        raise ValueError("Codex CLI is unavailable for causal reflection")
    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["claim", "mechanism", "proposed_intervention", "uncertainty", "falsifier"],
        "properties": {
            "claim": {"type": "string", "maxLength": 1600},
            "mechanism": {"type": "string", "maxLength": 1600},
            "proposed_intervention": {"type": "object", "maxProperties": 12},
            "uncertainty": {"type": "string", "maxLength": 1600},
            "falsifier": {"type": "string", "maxLength": 1600},
        },
    }
    prompt = (
        "You are the causal-reflection agent in an EDA experiment system. The supplied EDAIR packets are "
        "evidence summaries with explicit loss manifests. Propose ONE narrow, falsifiable hypothesis. "
        "Do not claim that correlation is cause. Do not invent missing measurements. The intervention must be "
        "a data-only bounded parameter experiment, never a shell command or source edit. State uncertainty and "
        "a concrete falsifier. Return only JSON matching the schema.\n\n"
        f"EDAIR_PACKETS={json.dumps(packets, ensure_ascii=False, sort_keys=True)}"
    )
    with tempfile.TemporaryDirectory(prefix="openroad-causal-reflection-") as raw:
        root = Path(raw); schema_path, output_path = root / "schema.json", root / "reflection.json"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        env = {key: os.environ[key] for key in ("HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "TZ", "CODEX_HOME") if key in os.environ}
        completed = subprocess.run(
            [executable, "exec", "--ephemeral", "--ignore-rules", "--skip-git-repo-check",
             "--sandbox", "read-only", "--model", "gpt-5.6-terra", "--output-schema", str(schema_path),
             "--output-last-message", str(output_path), "--color", "never", "-"],
            input=prompt, cwd=root, env=env, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=180, check=False,
        )
        if completed.returncode != 0 or not output_path.is_file():
            detail = "\n".join((completed.stderr or completed.stdout).splitlines()[-10:])
            raise RuntimeError(detail or "Codex returned no causal reflection")
        try:
            result = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("Codex returned invalid causal-reflection JSON") from exc
    if not all(isinstance(result.get(key), str) and result[key].strip() for key in ("claim", "mechanism", "uncertainty", "falsifier")):
        raise RuntimeError("causal reflection omitted a required textual field")
    intervention = result.get("proposed_intervention")
    if not isinstance(intervention, dict) or not intervention:
        raise RuntimeError("causal reflection omitted a bounded intervention")
    forbidden = {"command", "shell", "script", "path", "credential", "api_key"}
    if forbidden & set(intervention):
        raise RuntimeError("causal reflection proposed an unsafe intervention")
    return result


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
                elif re.fullmatch(r"/api/runtime/runs/[^/]+/artifacts/[^/]+/excerpt", path):
                    parts = path.split("/")
                    values = parse_qs(parsed.query)
                    self._json(state.runtime_artifact_excerpt(
                        unquote(parts[4]), unquote(parts[6]),
                        owner_id=direct_owner, include_legacy=session.legacy_access,
                        offset=int((values.get("offset") or [0])[0]),
                        length=int((values.get("length") or [16_384])[0])))
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
                        include_legacy=session.legacy_access,
                        focus=(query.get("focus") or [None])[0]))
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
                elif path == "/api/knowledge/public":
                    self._json(state.public_knowledge(parse_qs(parsed.query)))
                elif path == "/api/taiwei/technology-matrix":
                    self._json(state.taiwei_technology_matrix())
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
                elif path.startswith("/api/v2/closed-loops/"):
                    checkpoint = state.pipeline_checkpoints.get(
                        unquote(path.removeprefix("/api/v2/closed-loops/")))
                    if (checkpoint["pipeline_kind"] != "bo-gp-closed-loop-v2"
                            or checkpoint.get("owner_id") not in {None, session.user_id}
                            and not session.legacy_access):
                        raise KeyError(path)
                    self._json(checkpoint)
                elif re.fullmatch(r"/api/spec/sessions/[^/]+", path):
                    self._json(state.get_spec_session(
                        unquote(path.split("/")[-1]), owner_id=session.user_id,
                        include_legacy=session.legacy_access))
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

                def autonomous_product_request(payload: dict[str, Any]) -> dict[str, Any]:
                    """Keep research knobs out of the sole v2 product entry.

                    Baseline values, BO bounds, repetition count, budget, stall
                    rule and target stage are platform policy.  They remain
                    configurable through direct research harnesses, never by a
                    browser/client pretending to run the autonomous product.
                    """
                    allowed = {"design_id", "clock", "platform", "objective_profile"}
                    unexpected = sorted(set(payload) - allowed)
                    if unexpected:
                        raise ValueError(
                            "the autonomous v2 entry does not accept manual search controls: "
                            + ", ".join(unexpected)
                        )
                    return scoped(payload)

                def autonomous_rtl_request(payload: dict[str, Any]) -> dict[str, Any]:
                    if payload:
                        raise ValueError(
                            "the automatic RTL product entry accepts no model, testbench, "
                            "cost, step, revision, or execution controls"
                        )
                    return scoped({})

                def autonomous_resume_request(payload: dict[str, Any]) -> dict[str, Any]:
                    """The service, not the browser, owns the loop execution budget."""
                    if payload:
                        raise ValueError(
                            "the autonomous v2 resume entry accepts no transition, seed, "
                            "repetition, round, or search controls"
                        )
                    return scoped({})

                if path == "/api/spec/sessions":
                    self._json(state.create_spec_session(scoped(self._read_json())), HTTPStatus.CREATED)
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
                match = re.fullmatch(r"/api/spec/sessions/([^/]+)/turn", path)
                if match:
                    self._json(state.add_spec_turn(
                        unquote(match.group(1)), scoped(self._read_json())))
                    return
                match = re.fullmatch(r"/api/spec/sessions/([^/]+)/materialize-spec", path)
                if match:
                    self._json(state.materialize_specir(
                        unquote(match.group(1)), scoped(self._read_json())),
                        HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/rtl/specs/([^/]+)/run-to-baseline", path)
                if match:
                    self._json(state.run_automated_rtl_pipeline(
                        unquote(match.group(1)), autonomous_rtl_request(self._read_json()), owner_id=session.user_id,
                        include_legacy=session.legacy_access), HTTPStatus.CREATED)
                    return
                if path == "/api/v2/closed-loops":
                    self._json(state.start_bayesian_closed_loop(
                        autonomous_product_request(self._read_json()), owner_id=session.user_id,
                        include_legacy=session.legacy_access), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/v2/closed-loops/([^/]+)/run-to-boundary", path)
                if match:
                    self._json(state.run_bayesian_closed_loop_to_boundary(
                        unquote(match.group(1)), autonomous_resume_request(self._read_json()),
                        owner_id=session.user_id,
                        include_legacy=session.legacy_access))
                    return
                if path == "/api/research/protocols":
                    self._json(state.preregister_paper_protocol(scoped(self._read_json())), HTTPStatus.CREATED)
                    return
                if path == "/api/research/compare-arms":
                    self._json(state.summarize_paper_arms(scoped(self._read_json())))
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
                match = re.fullmatch(r"/api/designs/([^/]+)/circuitops-export", path)
                if match:
                    self._json(state.designs.circuitops_export(
                        unquote(match.group(1)), owner_id=session.user_id,
                        include_legacy=session.legacy_access), HTTPStatus.CREATED)
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
    state = ApiState(
        args.db,
        args.upload_root,
        args.orfs_root,
        design_root=args.design_root,
        legacy_root=args.legacy_root,
        runtime_db_path=args.runtime_db,
        optimization_db_path=args.optimization_db,
        auth_db_path=args.auth_db,
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
