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
import sys
import uuid
from http import HTTPStatus
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

from openroad_platform_contracts import LearningContext, RunRequest, RunStage  # noqa: E402
from openroad_platform_analysis import (  # noqa: E402
    LearningCollector, OptimizationStudyStore, PublicKnowledgeRegistry, RecommendationStore,
    TenantLearningStore, automation_envelope, build_recommendation,
    load_public_manifest,
)
from openroad_platform_execution import (  # noqa: E402
    PluginRegistry, ToolchainConfig, build_craft_flow_plan, build_orfs_task,
    craft_capability_matrix, craft_plan_to_task, orfs_plugin_manifest,
)
from openroad_platform_scheduler import (  # noqa: E402
    ALLOWED_MODELS, CampaignStore, CodexCliSpecProvider, JobStore,
    InMemorySecretBroker, NaturalLanguageTaskCompiler,
    OpenAICompatibleSpecProvider, ProviderProfile, ProviderProfileStore,
    RuleBasedSpecProvider, RuntimeStore,
    SpecConversationManager, SpecConversationStore, StageAwareCampaignManager,
    WorkflowRuntime,
)
try:  # Supports both `python apps/api/app.py` and package imports in tests.
    from .services import DesignService  # type: ignore[attr-defined]
except ImportError:
    from services import DesignService  # type: ignore[no-redef]


MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 2 * MAX_BODY_BYTES + 64 * 1024
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9_.-]+$")


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
        byok_transport_secure: bool | None = None,
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
        self.runtime_store = RuntimeStore(runtime_db_path or local_state / "runtime.db")
        self.campaign_store = CampaignStore(campaign_db_path or local_state / "campaign.db")
        self.spec_store = SpecConversationStore(spec_db_path or state_root / "spec.db")
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
        self.provider_profiles = ProviderProfileStore(state_root / "provider-profiles.db")
        self.secret_broker = InMemorySecretBroker(default_ttl_seconds=8 * 3600)
        self.recommendation_store = RecommendationStore(state_root / "recommendations.db")
        self._spec_provider_bindings: dict[str, dict[str, str]] = {}
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
        self.runtime = WorkflowRuntime(
            self.runtime_store, PluginRegistry([orfs_plugin_manifest(toolchain)]),
            workspace_root=state_root / "runtime-workspaces",
        )
        self.stage_campaigns = StageAwareCampaignManager(self.campaign_store, self.runtime)

    def health(self) -> dict[str, Any]:
        openroad = _find_tool("openroad", ROOT.parent / "bin" / "openroad")
        yosys = _find_tool("yosys", ROOT.parent / "bin" / "yosys")
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
            "byok_input_enabled": self.byok_transport_secure,
        }
        payload.update(self.designs.readiness())
        return payload

    @staticmethod
    def projects() -> dict[str, Any]:
        return {
            "projects": [
                {
                    "id": "circuit-studio",
                    "name": "Circuit Studio",
                    "description": "自然语言生成 RTL、门级网表、电路图和结构分析",
                    "route": "design",
                    "status": "available",
                },
                {
                    "id": "physical-flow",
                    "name": "RTL-to-GDS Flow",
                    "description": "六阶段 ORFS 实现、硬门禁、产物和物理分析",
                    "route": "flow",
                    "status": "available",
                },
                {
                    "id": "taiwei-3d",
                    "name": "TaiWei 3D",
                    "description": "固定官方工具链的双层 gcd、HBT 指标、真实产物与可重放证据",
                    "route": "three-d",
                    "status": "available",
                },
                {
                    "id": "self-evolution",
                    "name": "Evidence-driven Evolution",
                    "description": "Evidence RAG、BO/GP、Pareto 前沿与 RL shadow 证据",
                    "route": "evolve",
                    "status": "available",
                },
                {
                    "id": "ic-craft",
                    "name": "IC Craft",
                    "description": "后端中立 FlowPlan、OpenROAD/ORFS 执行与商业脚本能力对照",
                    "route": "craft",
                    "status": "available",
                },
            ],
            "extension_contract": {
                "manifest": "project id, name, description, route, status",
                "runtime": "adapter submits durable requests and returns evidence",
            },
        }

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        return [self._serialize_job(job) for job in self.store.list(limit=limit)]

    def get_run(self, run_id: str) -> dict[str, Any]:
        job = self.store.get(run_id)
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
            labels={"source": "web"},
        )
        request.validate(require_rtl=False)
        rtl_path.parent.mkdir(parents=True, exist_ok=False)
        rtl_path.write_text(source, encoding="utf-8")
        return self._serialize_job(self.store.submit(request))

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        return self._serialize_job(self.store.request_cancel(run_id))

    def list_runtime_runs(self, limit: int = 50) -> dict[str, Any]:
        return {"runs": [{"run_id": run.run_id, "task_id": run.task_id,
                           "status": run.status.value, "created_at": run.created_at,
                           "started_at": run.started_at, "ended_at": run.ended_at,
                           "plugin_id": run.task_spec.plugin_id,
                           "project_id": run.task_spec.project_id,
                           "design_id": run.task_spec.design_id}
                          for run in self.runtime_store.list_runs(limit=limit)]}

    def get_runtime_run(self, run_id: str) -> dict[str, Any]:
        payload = self.runtime_store.describe_run(run_id)
        for stage in payload.get("stages", []):
            for attempt in stage.get("attempts", []):
                for artifact in attempt.get("artifacts", []):
                    artifact["url"] = (
                        f"/api/runtime/runs/{run_id}/artifacts/{artifact['artifact_id']}"
                    )
        task = payload.get("run", {}).get("task_spec", {})
        if task.get("plugin_id") == "taiwei-pin-3d":
            payload["three_d"] = self._three_d_view(payload)
        return payload

    def runtime_artifact(self, run_id: str, artifact_id: str) -> tuple[Path, str]:
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

    def cancel_runtime_run(self, run_id: str) -> dict[str, Any]:
        self.runtime_store.request_cancel(run_id)
        return self.runtime_store.describe_run(run_id)

    def list_campaigns(self) -> dict[str, Any]:
        return {"campaigns": [self.get_campaign(item["campaign_id"])
                              for item in self.campaign_store.list()]}

    def get_campaign(self, campaign_id: str) -> dict[str, Any]:
        try:
            return self.stage_campaigns.describe(campaign_id)
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

    def list_optimization_studies(self) -> dict[str, Any]:
        return {"studies": self.optimization_store.list()}

    def get_optimization_study(self, study_id: str) -> dict[str, Any]:
        return self.optimization_store.describe(study_id)

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
        allowed_hosts = {"api.openai.com", "localhost", "127.0.0.1", "::1"}
        allowed_hosts.update(item.strip().lower() for item in os.environ.get(
            "OPENROAD_PLATFORM_PROVIDER_ALLOW_HOSTS", "").split(",") if item.strip())
        if provider_host.lower() not in allowed_hosts:
            raise ValueError("Provider host is not in the administrator egress allowlist")
        profile_id = self.provider_profiles.save(profile)
        handle = self.secret_broker.put(api_key, owner_id=owner_id, session_id=session_id)
        return {"profile_id": profile_id, "owner_id": owner_id, "session_id": session_id,
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

    def collect_runtime_learning(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run = self.runtime_store.get_run(run_id)
        rtl = run.task_spec.inputs.get("rtl")
        rtl_sha = rtl.get("sha256") if isinstance(rtl, dict) else run.task_spec.inputs.get("rtl_sha256")
        if not isinstance(rtl_sha, str):
            raise ValueError("Runtime task has no immutable RTL fingerprint")
        context = LearningContext(
            design_id=run.task_spec.design_id, design_fingerprint=rtl_sha,
            platform=str(run.task_spec.parameters.get("platform") or "unknown"),
            pdk_id=str(payload.get("pdk_id") or ""),
            toolchain_id=str(payload.get("toolchain_id") or ""),
            flow_stage=str(run.task_spec.parameters.get("target_stage") or "finish"),
            metric_parser_version=str(payload.get("metric_parser_version") or ""),
        )
        receipt = self.learning_collector.collect(
            run_id, context, tenant_id=str(payload.get("tenant_id") or ""),
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

    def _byok_transport_available(self) -> bool:
        return self.byok_transport_secure

    def create_recommendation(self, study_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        owner_id = str(payload.get("owner_id") or "local-user")
        study = self.optimization_store.get(study_id)
        proposals = self.optimization_store.proposals(study_id)
        if not proposals:
            raise ValueError("Study has no optimizer proposal")
        recommendation = build_recommendation(
            study, proposals[-1], self.optimization_store.observations(study_id),
            held_out_error=(float(payload["held_out_error"])
                            if payload.get("held_out_error") is not None else None),
            interval_coverage=(float(payload["interval_coverage"])
                               if payload.get("interval_coverage") is not None else None),
            worst_case_cost_seconds=float(payload.get("worst_case_cost_seconds", 7200)),
        )
        self.recommendation_store.save(owner_id, recommendation)
        envelope = automation_envelope(
            recommendation, exact_context=payload.get("exact_context", True) is True,
            study_opt_in=payload.get("study_opt_in") is True,
            budget_available=payload.get("budget_available", True) is True,
        )
        return {"recommendation": recommendation.to_dict(),
                "automation_envelope": envelope.to_dict()}

    def decide_recommendation(self, recommendation_id: str,
                              payload: dict[str, Any]) -> dict[str, Any]:
        owner_id = str(payload.get("owner_id") or "local-user")
        recommendation = self.recommendation_store.get(owner_id, recommendation_id)
        study = self.optimization_store.get(recommendation.study_id)
        bounds = {item.name: (item.lower, item.upper) for item in study.parameter_space}
        decision = self.recommendation_store.decide(
            owner_id, recommendation_id, action=str(payload.get("action") or ""),
            parameters=payload.get("parameters"), comment=str(payload.get("comment") or ""),
            parameter_bounds=bounds,
        )
        return {"decision": decision.to_dict(), "execution_started": False,
                "next_step": "Use the validated ExperimentPlan/Campaign endpoint to execute."}

    def list_recommendations(self, owner_id: str) -> dict[str, Any]:
        return {"recommendations": self.recommendation_store.list(owner_id)}

    def craft_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        design_id = str(payload.get("design_id") or "").strip()
        design = self.designs.get(design_id)
        plan = build_craft_flow_plan(
            self.designs.rtl_path(design_id), project_id=str(payload.get("project_id") or "openroad-platform"),
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
        result = {"flow_plan": plan.to_dict(), "capability_matrix": craft_capability_matrix(plan),
                  "backend": backend, "task_spec": task.to_dict(), "execution_started": False}
        if payload.get("execute") is True:
            if backend != "openroad-orfs":
                raise ValueError("ImplCraft is script-generation-only in this deployment")
            run = self.runtime_store.find_run_by_task_id(task.task_id)
            if run is None:
                run = self.runtime.submit(task, capability="eda.rtl_to_gds")
            result["execution_started"] = True
            result["runtime"] = self.get_runtime_run(run.run_id)
        return result

    def create_stage_campaign(self, payload: dict[str, Any]) -> dict[str, Any]:
        design_id = str(payload.get("design_id") or "").strip()
        design = self.designs.get(design_id)
        base = build_orfs_task(
            self.designs.rtl_path(design_id), project_id="openroad-platform",
            design_id=design_id, top=design["module"],
            target_stage=str(payload.get("target_stage") or "finish"),
            clock_period_ns=_number(payload, "clock_period_ns", 10.0),
            core_utilization_pct=_number(payload, "core_utilization_pct", 10.0),
            place_density=_number(payload, "place_density", 0.45),
            stage_timeout_seconds=int(_number(payload, "stage_timeout_seconds", 3600)),
        )
        grid = payload.get("parameter_grid") or {}
        if not isinstance(grid, dict):
            raise ValueError("parameter_grid must be an object")
        stage_budgets = payload.get("stage_budgets") or {}
        if not isinstance(stage_budgets, dict):
            raise ValueError("stage_budgets must be an object")
        campaign_id = self.stage_campaigns.create_grid(
            str(payload.get("name") or f"stage-search-{design_id}"), base, grid,
            max_parallel=int(payload.get("max_parallel", 1)),
            stage_budgets=stage_budgets,
            objective_metric=_optional_string(payload.get("objective_metric")),
            direction=str(payload.get("direction") or "min"),
            top_k=int(payload.get("top_k", 3)),
            max_repairs=int(payload.get("max_repairs", 2)),
            max_total_runs=int(payload.get("max_total_runs", 64)),
        )
        return self.stage_campaigns.describe(campaign_id)

    def create_spec_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        design_id = _optional_string(payload.get("design_id"))
        design = self.designs.get(design_id) if design_id else None
        provider = self._spec_provider_from_payload(payload)
        budgets = payload.get("budgets")
        if budgets is not None and not isinstance(budgets, dict):
            raise ValueError("budgets must be an object")
        result = SpecConversationManager(self.spec_store, provider).create(
            message=str(payload.get("message") or ""), design_id=design_id,
            design_context=design, budgets=budgets,
        )
        if provider.provider_name == "openai-compatible-byok":
            self._spec_provider_bindings[result["session_id"]] = {
                key: str(payload[key]) for key in
                ("owner_id", "session_id", "profile_id", "secret_handle")
            }
        return result

    def get_spec_session(self, session_id: str) -> dict[str, Any]:
        session = self.spec_store.get(session_id)
        if session.get("run_id"):
            session["runtime"] = self.runtime_store.describe_run(session["run_id"])
        return session

    def add_spec_turn(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = self.spec_store.get(session_id)
        design_id = session.get("design_id")
        design = self.designs.get(design_id) if design_id else None
        provider = self._spec_provider_for_session(session_id, session)
        return SpecConversationManager(self.spec_store, provider).turn(
            session_id, str(payload.get("message") or ""), design_context=design,
        )

    def execute_spec_session(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = self.spec_store.get(session_id)
        design_id = session.get("design_id")
        if design_id:
            design = self.designs.get(design_id)
        else:
            rtl = session["state"].get("rtl_source")
            if not isinstance(rtl, str) or not rtl.strip():
                raise ValueError("Session has neither a registered design nor proposed RTL")
            design = self.designs.import_rtl(
                filename=f"{session['state'].get('top') or 'design'}.v", source=rtl,
                description=session["state"].get("functionality"),
            )
            design_id = design["id"]
        provider = self._spec_provider_for_session(session_id, session)
        task = SpecConversationManager(self.spec_store, provider).compile(
            session_id, rtl_path=self.designs.rtl_path(design_id), design_id=design_id,
            confirmed=payload.get("confirmed") is True,
        )
        run = self.runtime_store.find_run_by_task_id(task.task_id)
        if run is None:
            run = self.runtime.submit(task, capability="eda.rtl_to_gds")
        self.spec_store.bind_run(session_id, run.run_id, design_id=design_id)
        return self.get_spec_session(session_id)

    def _spec_provider_from_payload(self, payload: dict[str, Any]):
        name = str(payload.get("provider") or "deterministic")
        model = _optional_string(payload.get("model"))
        if name == "openai-compatible-byok":
            required = ("owner_id", "session_id", "profile_id", "secret_handle")
            if any(not payload.get(key) for key in required):
                raise ValueError("BYOK provider requires owner/session/profile/secret handle")
            profile = self.provider_profiles.get(str(payload["profile_id"]),
                                                  owner_id=str(payload["owner_id"]))
            return OpenAICompatibleSpecProvider(
                profile, self.secret_broker, str(payload["secret_handle"]),
                owner_id=str(payload["owner_id"]), session_id=str(payload["session_id"]),
                profile_store=self.provider_profiles,
            )
        return self._spec_provider(name, model)

    def _spec_provider_for_session(self, session_id: str, session: dict[str, Any]):
        if session["provider"] != "openai-compatible-byok":
            return self._spec_provider(session["provider"], session["model"])
        binding = self._spec_provider_bindings.get(session_id)
        if binding is None:
            raise ValueError("BYOK session binding expired; re-enter the API key")
        return self._spec_provider_from_payload({"provider": session["provider"], **binding})

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

    def cancel_campaign(self, campaign_id: str) -> dict[str, Any]:
        for member in self.campaign_store.members(campaign_id):
            if member.run_id:
                self.runtime_store.request_cancel(member.run_id)
        return self.get_campaign(campaign_id)

    def submit_design_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        design_id = str(payload.get("design_id") or "").strip()
        design = self.designs.get(design_id)
        request = RunRequest(
            rtl_path=str(self.designs.rtl_path(design_id)),
            top=_optional_string(payload.get("top")) or design["module"],
            clock=_optional_string(payload.get("clock")),
            clock_period_ns=_number(payload, "clock_period_ns", 10.0),
            platform=str(payload.get("platform") or "nangate45"),
            target_stage=RunStage(str(payload.get("target_stage") or "finish")),
            core_utilization_pct=_number(payload, "core_utilization_pct", 10.0),
            place_density=_number(payload, "place_density", 0.45),
            stage_timeout_seconds=int(_number(payload, "stage_timeout_seconds", 3600)),
            labels={"source": "design", "design_id": design_id},
        )
        request.validate()
        return self._serialize_job(self.store.submit(request))

    def submit_runtime_design_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        design_id = str(payload.get("design_id") or "").strip()
        design = self.designs.get(design_id)
        task = build_orfs_task(
            self.designs.rtl_path(design_id), project_id="openroad-platform",
            design_id=design_id,
            top=_optional_string(payload.get("top")) or design["module"],
            clock=_optional_string(payload.get("clock")),
            clock_period_ns=_number(payload, "clock_period_ns", 10.0),
            core_utilization_pct=_number(payload, "core_utilization_pct", 10.0),
            place_density=_number(payload, "place_density", 0.45),
            target_stage=str(payload.get("target_stage") or "finish"),
            stage_timeout_seconds=int(_number(payload, "stage_timeout_seconds", 3600)),
            labels={"source": "web-runtime", "design_id": design_id},
        )
        run = self.runtime.submit(task, capability="eda.rtl_to_gds")
        return self.get_runtime_run(run.run_id)

    def compile_task_intent(self, payload: dict[str, Any]) -> dict[str, Any]:
        design_id = str(payload.get("design_id") or "").strip()
        intent = str(payload.get("intent") or "").strip()
        if not design_id:
            raise ValueError("design_id is required")
        design = self.designs.get(design_id)
        task = NaturalLanguageTaskCompiler().compile(
            intent, project_id="openroad-platform", design_id=design_id,
            rtl_path=self.designs.rtl_path(design_id), top=design["module"],
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _optional_string(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _number(payload: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(payload.get(key, default))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be numeric") from exc


def make_handler(state: ApiState) -> type[BaseHTTPRequestHandler]:
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
            try:
                if path == "/api/health":
                    self._json(state.health())
                elif path == "/api/projects":
                    self._json(state.projects())
                elif path == "/api/designs":
                    self._json({"designs": state.designs.list()})
                elif re.fullmatch(r"/api/designs/[^/]+/schematic\.svg", path):
                    design_id = unquote(path.split("/")[3])
                    self._text(state.designs.schematic(design_id), "image/svg+xml; charset=utf-8")
                elif re.fullmatch(r"/api/designs/[^/]+/source", path):
                    design_id = unquote(path.split("/")[3])
                    kind = parse_qs(parsed.query).get("kind", ["rtl"])[0]
                    if kind not in {"rtl", "netlist"}:
                        raise ValueError("kind must be rtl or netlist")
                    self._text(state.designs.source(design_id, kind), "text/plain; charset=utf-8")
                elif path.startswith("/api/designs/"):
                    self._json(state.designs.get(
                        unquote(path.removeprefix("/api/designs/")), include_source=True
                    ))
                elif path == "/api/runs":
                    self._json({"runs": state.list_runs()})
                elif path == "/api/runtime/runs":
                    self._json(state.list_runtime_runs())
                elif re.fullmatch(r"/api/runtime/runs/[^/]+/artifacts/[^/]+", path):
                    parts = path.split("/")
                    artifact_path, content_type = state.runtime_artifact(
                        unquote(parts[4]), unquote(parts[6]))
                    self._file(artifact_path, content_type)
                elif path.startswith("/api/runtime/runs/"):
                    self._json(state.get_runtime_run(
                        unquote(path.removeprefix("/api/runtime/runs/"))))
                elif path == "/api/campaigns":
                    self._json(state.list_campaigns())
                elif path == "/api/optimization/studies":
                    self._json(state.list_optimization_studies())
                elif path == "/api/knowledge/public":
                    self._json(state.public_knowledge(parse_qs(parsed.query)))
                elif path == "/api/providers":
                    owner_id = (parse_qs(parsed.query).get("owner_id") or ["local-user"])[0]
                    self._json(state.list_provider_profiles(owner_id))
                elif path == "/api/recommendations":
                    owner_id = (parse_qs(parsed.query).get("owner_id") or ["local-user"])[0]
                    self._json(state.list_recommendations(owner_id))
                elif path == "/api/learning/observations":
                    self._json(state.list_learning_observations(parse_qs(parsed.query)))
                elif path.startswith("/api/optimization/studies/"):
                    self._json(state.get_optimization_study(
                        unquote(path.removeprefix("/api/optimization/studies/"))))
                elif re.fullmatch(r"/api/spec/sessions/[^/]+", path):
                    self._json(state.get_spec_session(unquote(path.split("/")[-1])))
                elif path.startswith("/api/campaigns/"):
                    self._json(state.get_campaign(
                        unquote(path.removeprefix("/api/campaigns/"))))
                elif path.startswith("/api/runs/"):
                    self._json(state.get_run(unquote(path.removeprefix("/api/runs/"))))
                elif path in {"/", "/index.html"}:
                    self._file(WEB_ROOT / "index.html", "text/html; charset=utf-8")
                elif path == "/assets/app.css":
                    self._file(WEB_ROOT / "assets" / "app.css", "text/css; charset=utf-8")
                elif path == "/assets/app.js":
                    self._file(WEB_ROOT / "assets" / "app.js", "text/javascript; charset=utf-8")
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
                if path == "/api/runs":
                    self._json(state.submit_run(self._read_json()), HTTPStatus.CREATED)
                    return
                if path == "/api/runs/from-design":
                    self._json(state.submit_design_run(self._read_json()), HTTPStatus.CREATED)
                    return
                if path == "/api/runtime/runs/from-design":
                    self._json(state.submit_runtime_design_run(self._read_json()),
                               HTTPStatus.CREATED)
                    return
                if path == "/api/tasks/compile":
                    self._json(state.compile_task_intent(self._read_json()))
                    return
                if path == "/api/spec/sessions":
                    self._json(state.create_spec_session(self._read_json()), HTTPStatus.CREATED)
                    return
                if path == "/api/providers":
                    self._json(state.save_provider_profile(self._read_json()), HTTPStatus.CREATED)
                    return
                if path == "/api/providers/secrets/revoke":
                    self._json(state.revoke_provider_secret(self._read_json()))
                    return
                if path == "/api/craft/plans":
                    self._json(state.craft_plan(self._read_json()), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/optimization/studies/([^/]+)/recommend", path)
                if match:
                    self._json(state.create_recommendation(
                        unquote(match.group(1)), self._read_json()), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/recommendations/([^/]+)/decision", path)
                if match:
                    self._json(state.decide_recommendation(
                        unquote(match.group(1)), self._read_json()), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/runtime/runs/([^/]+)/collect-learning", path)
                if match:
                    self._json(state.collect_runtime_learning(
                        unquote(match.group(1)), self._read_json()), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/spec/sessions/([^/]+)/turn", path)
                if match:
                    self._json(state.add_spec_turn(unquote(match.group(1)), self._read_json()))
                    return
                match = re.fullmatch(r"/api/spec/sessions/([^/]+)/execute", path)
                if match:
                    self._json(state.execute_spec_session(
                        unquote(match.group(1)), self._read_json()), HTTPStatus.CREATED)
                    return
                if path == "/api/campaigns/stage-aware":
                    self._json(state.create_stage_campaign(self._read_json()), HTTPStatus.CREATED)
                    return
                if path == "/api/designs/generate":
                    payload = self._read_json()
                    self._json(
                        state.designs.generate(str(payload.get("description") or "")),
                        HTTPStatus.CREATED,
                    )
                    return
                if path == "/api/designs/import":
                    payload = self._read_json()
                    self._json(state.designs.import_rtl(
                        filename=str(payload.get("filename") or "design.v"),
                        source=str(payload.get("rtl_source") or ""),
                        description=_optional_string(payload.get("description")),
                    ), HTTPStatus.CREATED)
                    return
                match = re.fullmatch(r"/api/runs/([^/]+)/cancel", path)
                if match:
                    self._json(state.cancel_run(unquote(match.group(1))))
                    return
                match = re.fullmatch(r"/api/runtime/runs/([^/]+)/cancel", path)
                if match:
                    self._json(state.cancel_runtime_run(unquote(match.group(1))))
                    return
                match = re.fullmatch(r"/api/campaigns/([^/]+)/cancel", path)
                if match:
                    self._json(state.cancel_campaign(unquote(match.group(1))))
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

        def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._bytes(body, "application/json; charset=utf-8", status)

        def _text(
            self,
            value: str,
            content_type: str,
            status: HTTPStatus = HTTPStatus.OK,
        ) -> None:
            self._bytes(value.encode("utf-8"), content_type, status)

        def _bytes(self, body: bytes, content_type: str, status: HTTPStatus) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self._security_headers()
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
