"""Read models for the five-page platform UI.

This service intentionally performs no scheduling. It projects authoritative
stores into a stable, presentation-neutral API shape.
"""

from __future__ import annotations

from typing import Any

from openroad_platform_analysis import research_method_catalog


NAVIGATION = (
    {"id": "overview", "label": "Overview"},
    {"id": "frontend", "label": "Frontend Design"},
    {"id": "backend", "label": "Backend Design"},
    {"id": "projects", "label": "Projects & Results"},
    {"id": "evolution", "label": "Self-Evolution"},
)


class PlatformReadModel:
    def __init__(self, *, designs, runtime_store, campaign_store,
                 optimization_store, knowledge_registry, recommendation_store,
                 tenant_learning_store, extension_catalog: dict[str, Any]):
        self.designs = designs
        self.runtime_store = runtime_store
        self.campaign_store = campaign_store
        self.optimization_store = optimization_store
        self.knowledge_registry = knowledge_registry
        self.recommendation_store = recommendation_store
        self.tenant_learning_store = tenant_learning_store
        self.extension_catalog = extension_catalog

    def snapshot(self, *, owner_id: str | None = None, include_legacy: bool = False,
                 public: bool = False) -> dict[str, Any]:
        results = self.results(owner_id=owner_id, include_legacy=include_legacy)
        evolution = self.evolution(owner_id=owner_id, include_legacy=include_legacy)
        return {
            "schema_version": 1,
            "product": {
                "name": "OpenROAD Self-Evolving EDA Platform",
                "statement": "From design intent to verified silicon evidence.",
                "core": [
                    "Natural-language Spec-to-RTL-to-GDS design",
                    "Natural-language interaction with EDA workflows",
                    "Runtime-governed RTL-to-GDS implementation",
                    "Stage-aware Flow-Agent campaigns, diagnosis, and repair",
                    "Evidence-backed learning with human-controlled recommendations",
                    "Optional 3D IC, device/EM/SPICE, and source-code evolution",
                ],
            },
            "navigation": list(NAVIGATION),
            "architecture": {
                "control_plane": "Workflow Runtime is the only terminal-state authority",
                "execution_plane": "Versioned plugins run in isolated workspaces",
                "evidence_plane": "Artifacts, metrics, versions, and SHA-256 remain replayable",
                "learning_plane": "Observed runs and public knowledge remain provenance-separated",
            },
            "extensions": self.extension_catalog,
            "extension_families": [
                {
                    "id": "taiwei-3d", "name": "TaiWei 3D IC",
                    "summary": "Pinned two-tier physical implementation with HBT and cross-tier evidence.",
                    "status": "available", "execution": "Workflow Runtime",
                },
                {
                    "id": "dplevolve", "name": "DPLEvolve / Tool-Evolve",
                    "summary": "Optional OpenROAD source-code candidate generation and audited evaluation.",
                    "status": "available_on_demand", "execution": "User-configured long task",
                },
            ],
            "counts": {
                "designs": 0 if public else results["counts"]["designs"],
                "runtime_runs": 0 if public else results["counts"]["runtime_runs"],
                "campaigns": 0 if public else results["counts"]["campaigns"],
                "knowledge_sources": evolution["counts"]["knowledge_sources"],
                "optimization_studies": 0 if public else evolution["counts"]["optimization_studies"],
            },
        }

    def results(self, limit: int = 50, *, owner_id: str | None = None,
                include_legacy: bool = False) -> dict[str, Any]:
        designs = self.designs.list(
            limit=limit, owner_id=owner_id, include_legacy=include_legacy
        )
        all_runs = self.runtime_store.list_runs(limit=max(limit, 200))
        runtime_runs = [run for run in all_runs if _owned_task(
            run.task_spec, owner_id, include_legacy=include_legacy
        )][:limit]
        campaigns = [item for item in self.campaign_store.list()
                     if _owned_campaign(self.campaign_store, item["campaign_id"],
                                        owner_id, include_legacy=include_legacy)]
        records = []
        design_names = {item["id"]: item.get("module") or item["id"] for item in designs}
        for design in designs:
            records.append({
                "id": design["id"],
                "record_type": "design",
                "owner_id": str(design.get("owner_id") or ""),
                "project_type": "RTL / netlist",
                "name": design.get("module") or design["id"],
                "summary": design.get("description") or "Registered design",
                "status": "ready",
                "created_at": design.get("created_at"),
                "detail_url": f"/api/designs/{design['id']}",
                "visualization_url": f"/api/designs/{design['id']}/schematic.svg",
            })
        for run in runtime_runs:
            # Component acceptance fixtures remain queryable in Runtime, but
            # are not user projects and must not appear as design results.
            if run.task_spec.design_id == "edacraft-smoke":
                continue
            plugin = run.task_spec.plugin_id or "workflow"
            records.append({
                "id": run.run_id,
                "record_type": "runtime_run",
                "owner_id": str((run.task_spec.labels or {}).get("owner_id") or ""),
                "project_type": _project_type(plugin),
                "name": design_names.get(run.task_spec.design_id, _friendly_design_name(run.task_spec.design_id)),
                "summary": _project_type(plugin),
                "status": run.status.value,
                "created_at": run.created_at,
                "detail_url": f"/api/runtime/runs/{run.run_id}",
                "plugin_id": plugin,
                "replayable": run.status.value == "succeeded",
            })
        records.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return {
            "records": records[:limit],
            "counts": {
                "designs": len(designs),
                "runtime_runs": len(runtime_runs),
                "campaigns": len(campaigns),
            },
            "authority": "Runtime/database projection; no browser-owned process state",
        }

    def evolution(self, *, owner_id: str | None = None,
                  include_legacy: bool = False) -> dict[str, Any]:
        design_ids = {item["id"] for item in self.designs.list(
            limit=100, owner_id=owner_id, include_legacy=include_legacy
        )}
        studies = [item for item in self.optimization_store.list()
                   if owner_id is None or item.get("design_id") in design_ids]
        sources = self.knowledge_registry.list_sources()
        benchmarks = self.knowledge_registry.list_benchmarks()
        recommendations = self.recommendation_store.list(owner_id or "local-user")
        observations = self.tenant_learning_store.list(owner_id or "local-user", "openroad-platform")
        return {
            "counts": {
                "knowledge_sources": len(sources),
                "benchmarks": len(benchmarks),
                "optimization_studies": len(studies),
                "observed_samples": len(observations),
                "recommendations": len(recommendations),
            },
            "knowledge_sources": sources,
            "benchmarks": benchmarks,
            "studies": studies,
            "recommendations": recommendations,
            "research_methods": research_method_catalog()["methods"],
            "learning_loop": [
                "Retrieve traceable evidence",
                "Propose with BO / GP or RL shadow policy",
                "Ask for human decision when required",
                "Execute only through Workflow Runtime",
                "Verify artifacts and collect observed metrics",
                "Update the evidence store without rewriting history",
            ],
            "policy": {
                "predictions_are_observations": False,
                "rl_default": "shadow advice",
                "automatic_execution": "bounded by confidence, context, budget, and opt-in",
            },
        }


def _project_type(plugin_id: str) -> str:
    if plugin_id == "taiwei-pin-3d":
        return "3D physical design"
    if plugin_id == "edacraft-tcadcraft":
        return "Device / TCAD"
    if plugin_id == "edacraft-momcraft":
        return "Interconnect / EM"
    if plugin_id == "edacraft-cktcraft":
        return "Circuit / RF"
    if plugin_id in {"edacraft-rtlcraft", "rtlscout"}:
        return "RTL generation"
    if plugin_id == "dplevolve":
        return "Code evolution"
    return "Digital physical design"


def _friendly_design_name(design_id: str) -> str:
    if design_id.startswith("rtlscout-"):
        return design_id.removeprefix("rtlscout-").replace("_", " ")
    return "Linked design"


def _owned_task(task: Any, owner_id: str | None, *, include_legacy: bool) -> bool:
    if owner_id is None:
        return True
    recorded = str((task.labels or {}).get("owner_id") or "")
    return recorded == owner_id or (include_legacy and not recorded)


def _owned_campaign(store: Any, campaign_id: str, owner_id: str | None,
                    *, include_legacy: bool) -> bool:
    if owner_id is None:
        return True
    members = store.members(campaign_id)
    if not members:
        return include_legacy
    return any(_owned_task(item.task_spec, owner_id, include_legacy=include_legacy)
               for item in members)
