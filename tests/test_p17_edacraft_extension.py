from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from openroad_platform_contracts import PluginManifest, RuntimeStatus
from openroad_platform_execution import (
    EDACRAFT_COMPONENTS,
    IMPLCRAFT_PLUGIN_ID,
    ProcessAdapter,
    build_edacraft_task,
    edacraft_catalog,
    edacraft_plugin_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".external-src" / "edacraft"


def test_catalog_has_six_independent_components_and_preserves_implcraft():
    catalog = edacraft_catalog()
    components = catalog["components"]
    assert len(components) == 6
    assert {item["plugin_id"] for item in components} == {
        "edacraft-rtlcraft", "edacraft-edacode", "edacraft-tcadcraft",
        "edacraft-momcraft", "edacraft-cktcraft", IMPLCRAFT_PLUGIN_ID,
    }
    assert all(item["optional_extension"] is True for item in components)
    edacode = next(item for item in components if item["name"] == "EDACode")
    assert "not exposed" in edacode["safety_note"]


@pytest.mark.parametrize(
    "filename",
    ["rtlcraft.plugin.json", "edacode.plugin.json", "tcadcraft.plugin.json",
     "momcraft.plugin.json", "cktcraft.plugin.json"],
)
def test_repository_extension_manifests_are_contract_valid(filename):
    payload = json.loads((ROOT / "integrations" / "edacraft" / filename).read_text())
    PluginManifest.from_dict(payload)


def test_tasks_are_bounded_low_cost_smokes():
    for component in EDACRAFT_COMPONENTS:
        if component.slug == "implcraft":
            continue
        task = build_edacraft_task(component.slug, task_id=f"test-{component.slug}")
        assert task.plugin_id == component.plugin_id
        assert task.max_attempts == 1
        assert task.labels["optional_extension"] == "true"
        expected = "true" if component.slug in {"momcraft", "cktcraft"} else "false"
        assert task.labels["full_solver_executed"] == expected


def test_parameterized_specialist_tasks_preserve_explicit_inputs():
    tcad = build_edacraft_task(
        "tcadcraft", parameters={"length_nm": 12, "width_nm": 7, "height_nm": 4}
    )
    assert tcad.parameters["length_nm"] == 12
    mom = build_edacraft_task(
        "momcraft", parameters={"frequency_ghz": 2.4, "mesh_segments": 8}
    )
    assert mom.parameters["frequency_ghz"] == 2.4
    ckt = build_edacraft_task(
        "cktcraft", inputs={"spice_netlist": "V1 in 0 1\n.op\n.end"}
    )
    assert ckt.inputs["spice_netlist"].endswith(".end")


@pytest.mark.skipif(not (SOURCE / ".git").exists(), reason="pinned EDACraft cache absent")
@pytest.mark.parametrize("slug", ["rtlcraft", "edacode", "tcadcraft", "momcraft", "cktcraft"])
def test_pinned_component_smokes_use_runtime_adapter(slug, tmp_path):
    manifest = edacraft_plugin_manifest(slug, SOURCE, sys.executable)
    execution = ProcessAdapter().execute(
        manifest,
        build_edacraft_task(slug, task_id=f"p17-{slug}"),
        workspace=tmp_path / slug,
    )
    assert execution.result.status is RuntimeStatus.SUCCEEDED
    assert {item["kind"] for item in execution.artifacts} == set(
        next(item.artifacts for item in EDACRAFT_COMPONENTS if item.slug == slug)
    )
    report = json.loads((tmp_path / slug / "capability_report.json").read_text())
    assert report["safety"]["runtime_authoritative"] is True
    assert report["safety"]["full_solver_executed"] is (slug in {"momcraft", "cktcraft"})
    assert report["safety"]["signoff_claimed"] is False


@pytest.mark.skipif(not (SOURCE / ".git").exists(), reason="pinned EDACraft cache absent")
@pytest.mark.parametrize(
    ("slug", "inputs", "parameters", "metric"),
    [
        ("tcadcraft", {}, {"length_nm": 12, "width_nm": 7, "height_nm": 4},
         "tcadcraft.device_volume_nm3"),
        ("momcraft", {}, {"length_mm": 3, "width_mm": .4, "height_mm": .2,
                          "eps_eff": 4.1, "mesh_segments": 6, "frequency_ghz": 2.4},
         "momcraft.s21_magnitude"),
        ("cktcraft", {"spice_netlist": (
            "* user input\nV1 in 0 3.3\nR1 in out 1k\nR2 out 0 2k\n"
            ".op\n.print v(in) v(out) i(v1)\n.end"
        )}, {}, "cktcraft.converged"),
    ],
)
def test_parameterized_specialist_inputs_execute_upstream_solver(
    slug, inputs, parameters, metric, tmp_path
):
    execution = ProcessAdapter().execute(
        edacraft_plugin_manifest(slug, SOURCE, sys.executable),
        build_edacraft_task(slug, inputs=inputs, parameters=parameters),
        workspace=tmp_path / f"parameterized-{slug}",
    )
    assert execution.result.status is RuntimeStatus.SUCCEEDED
    assert metric in {item["name"] for item in execution.result.metrics}
