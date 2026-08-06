"""Backend-neutral IC Craft intent and Runtime TaskSpec adapters."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openroad_platform_contracts import TaskSpec

from .implcraft_plugin import build_implcraft_task
from .orfs_plugin import build_orfs_task


STAGES = ("synthesis", "floorplan", "placement", "cts", "route", "finish")
ORFS_STAGE = {"synthesis": "synth", "floorplan": "floorplan", "placement": "place",
              "cts": "cts", "route": "route", "finish": "finish"}


@dataclass(frozen=True)
class BackendNeutralFlowPlan:
    plan_id: str
    project_id: str
    design_id: str
    rtl_path: str
    rtl_sha256: str
    top: str
    clock: str
    clock_period_ns: float
    target_stage: str
    platform: str
    parameters: dict[str, float]
    qor_objectives: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()

    def validate(self) -> None:
        path = Path(self.rtl_path).expanduser().resolve()
        if not path.is_file() or _sha256(path) != self.rtl_sha256:
            raise ValueError("Craft FlowPlan RTL is missing or changed")
        if self.target_stage not in STAGES:
            raise ValueError("Unsupported Craft target stage")
        if not 0.01 <= float(self.clock_period_ns) <= 1000:
            raise ValueError("Craft clock period is outside policy")
        if self.platform not in {"nangate45", "asap7"}:
            raise ValueError("Unsupported Craft platform")
        allowed = {"core_utilization_pct", "place_density"}
        if set(self.parameters) - allowed:
            raise ValueError("Craft plan contains unsupported parameters")
        if "core_utilization_pct" in self.parameters and not 1 <= self.parameters["core_utilization_pct"] <= 99:
            raise ValueError("Craft core utilization is outside policy")
        if "place_density" in self.parameters and not 0.01 <= self.parameters["place_density"] <= 1:
            raise ValueError("Craft placement density is outside policy")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return dataclasses.asdict(self)


def build_craft_flow_plan(rtl_path: str | Path, *, project_id: str, design_id: str,
                          top: str, clock: str = "clk", clock_period_ns: float = 10.0,
                          target_stage: str = "finish", platform: str = "nangate45",
                          core_utilization_pct: float = 10.0,
                          place_density: float = 0.45,
                          qor_objectives: tuple[str, ...] = (),
                          required_capabilities: tuple[str, ...] = (),
                          plan_id: str | None = None) -> BackendNeutralFlowPlan:
    source = Path(rtl_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError("Craft RTL is missing")
    result = BackendNeutralFlowPlan(
        plan_id=plan_id or f"craft-plan-{uuid.uuid4().hex}", project_id=project_id,
        design_id=design_id, rtl_path=str(source), rtl_sha256=_sha256(source), top=top,
        clock=clock, clock_period_ns=float(clock_period_ns), target_stage=target_stage,
        platform=platform, parameters={"core_utilization_pct": float(core_utilization_pct),
                                       "place_density": float(place_density)},
        qor_objectives=tuple(qor_objectives),
        required_capabilities=tuple(required_capabilities),
    )
    result.validate()
    return result


def craft_capability_matrix(plan: BackendNeutralFlowPlan) -> dict[str, Any]:
    plan.validate()
    openroad_supported = {"synthesis", "floorplan", "placement", "cts", "route", "finish"}
    implcraft_supported = {"synthesis", "floorplan", "placement"}
    commercial_only = {"commercial-mmmc", "prime-time-signoff", "calibre-signoff",
                       "proprietary-database"}
    requested = set(plan.required_capabilities)
    return {
        "plan_id": plan.plan_id,
        "backends": {
            "openroad-orfs": {
                "mode": "executable-via-runtime",
                "target_stage_supported": plan.target_stage in openroad_supported,
                "unsupported_capabilities": sorted(requested & commercial_only),
                "commercial_signoff": False,
            },
            "implcraft-scriptgen": {
                "mode": "commercial-script-generation-only",
                "target_stage_supported": plan.target_stage in implcraft_supported,
                "unsupported_capabilities": sorted(requested - commercial_only),
                "commercial_eda_executed": False,
            },
        },
    }


def craft_plan_to_task(plan: BackendNeutralFlowPlan, backend: str, *,
                       commercial_tool_chain: str = "synopsys",
                       timeout_seconds: int = 7200) -> TaskSpec:
    plan.validate()
    matrix = craft_capability_matrix(plan)["backends"]
    if backend not in matrix:
        raise ValueError("Unknown Craft backend")
    capability = matrix[backend]
    if not capability["target_stage_supported"] or capability["unsupported_capabilities"]:
        raise ValueError(f"Craft backend cannot satisfy requested capabilities: {backend}")
    if backend == "openroad-orfs":
        return build_orfs_task(
            plan.rtl_path, project_id=plan.project_id, design_id=plan.design_id,
            top=plan.top, clock=plan.clock, clock_period_ns=plan.clock_period_ns,
            platform_name=plan.platform, target_stage=ORFS_STAGE[plan.target_stage],
            core_utilization_pct=plan.parameters["core_utilization_pct"],
            place_density=plan.parameters["place_density"], timeout_seconds=timeout_seconds,
            task_id=f"craft-orfs-{hashlib.sha256((plan.plan_id + backend).encode()).hexdigest()[:24]}",
            labels={"source": "ic-craft", "craft_plan_id": plan.plan_id,
                    "craft_backend": backend, "commercial_signoff": "false"},
        )
    return build_implcraft_task(
        plan.rtl_path, project_id=plan.project_id, design_id=plan.design_id,
        top=plan.top, clock=plan.clock, clock_period_ns=plan.clock_period_ns,
        tool_chain=commercial_tool_chain, stop_at=plan.target_stage,
        timeout_seconds=min(timeout_seconds, 600),
        task_id=f"craft-impl-{hashlib.sha256((plan.plan_id + backend).encode()).hexdigest()[:24]}",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
