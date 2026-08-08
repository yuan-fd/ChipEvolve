"""Contracts for the pinned, optional EDACraft extension pack.

EDACraft is a monorepo, but its six projects are deliberately exposed as
independent Runtime plugins.  This keeps their environments, evidence, and
security boundaries separate while retaining the existing ImplCraft plugin.
"""

from __future__ import annotations

import platform
import uuid
from dataclasses import dataclass
from pathlib import Path

from openroad_platform_contracts import PluginManifest, TaskSpec


EDACRAFT_UPSTREAM_COMMIT = "739eee0f3ced8fc3cbb6f01b6cc89414758fd898"
EDACRAFT_PLUGIN_VERSION = "1.2.0"
EDACRAFT_CKTCRAFT_SHA256 = "0040bc5d392fb3ad03ee4fc432d861233b2c75e4a9911c2459e8f7910e0a822c"
EDACRAFT_MOMCRAFT_SHA256 = "0fd99280dfd69befb3ad3c119e7da9ab539014c77e3e379dca4155dfa9e7e6bf"


@dataclass(frozen=True)
class EDACraftComponent:
    slug: str
    name: str
    layer: str
    capability: str
    execution_class: str
    summary: str
    smoke_mode: str
    artifacts: tuple[str, ...]
    safety_note: str

    @property
    def plugin_id(self) -> str:
        return f"edacraft-{self.slug}"

    def to_dict(self) -> dict[str, object]:
        return {
            "plugin_id": self.plugin_id,
            "name": self.name,
            "layer": self.layer,
            "capability": self.capability,
            "execution_class": self.execution_class,
            "summary": self.summary,
            "smoke_mode": self.smoke_mode,
            "artifacts": list(self.artifacts),
            "safety_note": self.safety_note,
            "optional_extension": True,
        }


EDACRAFT_COMPONENTS = (
    EDACraftComponent(
        "rtlcraft", "RTLCraft", "frontend", "eda.rtl.dsl_emit",
        "local-white-box-smoke",
        "Python hardware DSL, simulation, verification, SystemVerilog emission, and PPA analysis.",
        "dsl-emit", ("generated_rtl", "report", "source_snapshot"),
        "Generated RTL remains reviewable and must pass normal RTL validation before implementation.",
    ),
    EDACraftComponent(
        "edacode", "EDACode", "frontend", "eda.analog.agent.proposal",
        "constrained-proposal-only",
        "Analog and mixed-signal coding agent with an optional VS Code client.",
        "safe-proposal", ("agent_proposal", "report", "source_snapshot"),
        "Upstream shell and file-write tools are not exposed by this platform adapter.",
    ),
    EDACraftComponent(
        "tcadcraft", "TCADCraft", "device", "eda.tcad.physics_validation",
        "parameterized-structure-validation",
        "3D quantum-corrected semiconductor device simulation and device templates.",
        "physics-invariants", ("device_geometry", "device_view", "physics_validation", "report", "source_snapshot"),
        "Parameterized device geometry and physics invariants run locally; the inconsistent upstream full solver is not claimed.",
    ),
    EDACraftComponent(
        "momcraft", "MoMCraft", "interconnect", "eda.em.microstrip_solve",
        "parameterized-numerical-solver",
        "Method-of-Moments interconnect extraction and Touchstone S-parameter handling.",
        "microstrip-solve", ("s_parameters", "solver_result", "report", "source_snapshot"),
        "User-selected microstrip geometry and frequency run through the upstream numerical solver with bounded mesh cost; this is not sign-off EM.",
    ),
    EDACraftComponent(
        "cktcraft", "CktCraft", "circuit", "eda.spice.op",
        "bounded-user-netlist-solver",
        "SPICE/RF simulator supporting OP, DC, AC, HB, PSS, and transient analyses.",
        "dc-operating-point", ("simulation_result", "simulation_log", "report", "source_snapshot"),
        "A bounded user-supplied SPICE .op netlist runs in the upstream solver; external includes are forbidden and sign-off is not claimed.",
    ),
    EDACraftComponent(
        "implcraft", "ImplCraft", "backend", "eda.implcraft.scriptgen",
        "script-generation-only",
        "Digital backend automation and commercial-tool script generation.",
        "dry-run", ("implcraft_config", "implcraft_state", "eda_script", "report"),
        "Existing integration is preserved; no commercial EDA execution is claimed.",
    ),
)


def edacraft_component(slug: str) -> EDACraftComponent:
    for component in EDACRAFT_COMPONENTS:
        if component.slug == slug:
            return component
    raise KeyError(f"Unknown EDACraft component: {slug}")


def edacraft_catalog() -> dict[str, object]:
    return {
        "id": "edacraft-extension-pack",
        "name": "EDACraft Extension Pack",
        "source_commit": EDACRAFT_UPSTREAM_COMMIT,
        "license": "MIT-like, non-commercial restriction",
        "optional_extension": True,
        "components": [item.to_dict() for item in EDACRAFT_COMPONENTS],
    }


def edacraft_plugin_manifest(
    slug: str,
    source_root: str | Path,
    python_executable: str | Path,
    *,
    adapter_path: str | Path | None = None,
) -> PluginManifest:
    """Build one non-ImplCraft component manifest.

    ImplCraft keeps its established manifest constructor and adapter so P11
    evidence and task compatibility are not invalidated.
    """

    component = edacraft_component(slug)
    if slug == "implcraft":
        raise ValueError("Use implcraft_plugin_manifest for the preserved ImplCraft plugin")
    source = Path(source_root).expanduser().resolve()
    python = Path(python_executable).expanduser().resolve()
    if not (source / component.name).is_dir() or not python.is_file():
        raise FileNotFoundError(f"EDACraft {component.name} source or Python is missing")
    adapter = (Path(adapter_path).expanduser().resolve() if adapter_path else
               Path(__file__).with_name("edacraft_adapter.py").resolve())
    project_root = source.parent.parent
    runtime_root = project_root / ".tools" / "edacraft-runtime"
    environment = {
        "EDACRAFT_ROOT": str(source),
        "EDACRAFT_COMPONENT": component.name,
        "EDACRAFT_COMPONENT_SLUG": slug,
        "EDACRAFT_EXPECTED_COMMIT": EDACRAFT_UPSTREAM_COMMIT,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if slug == "cktcraft":
        environment["EDACRAFT_CKTCRAFT_BIN"] = str(
            runtime_root / "cktcraft-build" / "bin" / "rfsim"
        )
        environment["EDACRAFT_CKTCRAFT_SHA256"] = EDACRAFT_CKTCRAFT_SHA256
    if slug == "momcraft":
        environment["EDACRAFT_MOM_PYTHONPATH"] = str(runtime_root / "momcraft-python")
        environment["EDACRAFT_MOMCRAFT_SHA256"] = EDACRAFT_MOMCRAFT_SHA256
    manifest = PluginManifest(
        plugin_id=component.plugin_id,
        plugin_version=EDACRAFT_PLUGIN_VERSION,
        adapter_entry=(str(python), str(adapter)),
        capabilities=(component.capability,),
        supported_arch=(platform.machine(),),
        input_schema={"type": "object"},
        output_schema={"type": "object", "required": ["status", "artifacts"]},
        required_tools=("git", "python3"),
        default_timeout_seconds=120,
        artifact_rules=tuple(
            {"kind": kind, "required": True} for kind in component.artifacts
        ),
        environment=environment,
    )
    manifest.validate()
    return manifest


def build_edacraft_task(
    slug: str,
    *,
    project_id: str = "openroad-platform",
    design_id: str = "edacraft-smoke",
    timeout_seconds: int = 120,
    task_id: str | None = None,
    inputs: dict[str, object] | None = None,
    parameters: dict[str, object] | None = None,
) -> TaskSpec:
    component = edacraft_component(slug)
    if slug == "implcraft":
        raise ValueError("ImplCraft tasks require RTL; use build_implcraft_task")
    task = TaskSpec(
        task_id=task_id or f"{component.plugin_id}-{uuid.uuid4().hex}",
        project_id=project_id,
        design_id=design_id,
        plugin_id=component.plugin_id,
        inputs={"fixture": "p18-bounded-real-smoke",
                "prompt": "Propose a review-only CMOS inverter operating-point plan.",
                **dict(inputs or {})},
        parameters={"mode": component.smoke_mode, **dict(parameters or {})},
        resources={"execution_class": component.execution_class},
        timeout_seconds=timeout_seconds,
        max_attempts=1,
        expected_artifacts=component.artifacts,
        labels={
            "optional_extension": "true",
            "full_solver_executed": "true" if slug in {"cktcraft", "momcraft"} else "false",
        },
    )
    task.validate()
    return task
