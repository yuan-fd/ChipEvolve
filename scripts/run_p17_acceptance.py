#!/usr/bin/env python3
"""Replay the bounded EDACraft six-component Runtime acceptance."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for path in (
    ROOT / "packages" / "contracts" / "src",
    ROOT / "packages" / "execution" / "src",
    ROOT / "packages" / "scheduler" / "src",
):
    sys.path.insert(0, str(path))

from openroad_platform_execution import (  # noqa: E402
    PluginRegistry, build_edacraft_task, build_implcraft_task,
    edacraft_component, edacraft_plugin_manifest, implcraft_plugin_manifest,
)
from openroad_platform_scheduler import RuntimeStore, WorkflowRuntime  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_root.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = ROOT / ".external-src" / "edacraft"
    implcraft_python = ROOT / ".tools" / "venvs" / "implcraft" / "bin" / "python"
    slugs = ("rtlcraft", "edacode", "tcadcraft", "momcraft", "cktcraft")
    manifests = [
        edacraft_plugin_manifest(slug, source, Path(sys.executable)) for slug in slugs
    ]
    manifests.append(implcraft_plugin_manifest(source, implcraft_python))
    store = RuntimeStore(output / "runtime.db")
    runtime = WorkflowRuntime(
        store, PluginRegistry(manifests), workspace_root=output / "workspaces",
        worker_id="p17-acceptance", lease_seconds=30,
    )
    tasks = [
        build_edacraft_task(slug, task_id=f"p17-{slug}-smoke") for slug in slugs
    ]
    tasks.append(build_implcraft_task(
        ROOT / "tests" / "fixtures" / "p2_mux_2to1.v",
        project_id="openroad-platform", design_id="p17-mux",
        top="mux2", clock="clk", stop_at="floorplan",
        timeout_seconds=120, task_id="p17-implcraft-smoke",
    ))
    summaries = []
    for task in tasks:
        slug = task.plugin_id.removeprefix("edacraft-")
        component = edacraft_component(slug)
        run = runtime.submit(task, capability=component.capability)
        runtime.execute_once(run.run_id)
        detail = runtime.describe(run.run_id)
        attempt = [attempt for stage in detail["stages"]
                   for attempt in stage["attempts"]][-1]
        summaries.append({
            "component": component.name,
            "plugin_id": task.plugin_id,
            "run_id": run.run_id,
            "status": detail["run"]["status"],
            "execution_class": component.execution_class,
            "artifact_kinds": sorted(item["kind"] for item in attempt["artifacts"]),
            "artifact_sha256": {
                item["store_key"]: item["sha256"] for item in attempt["artifacts"]
            },
            "metrics": attempt["metrics"],
            "failure": attempt["failure"],
        })
    payload = {
        "schema_version": 1,
        "phase": "P17",
        "source_commit": "739eee0f3ced8fc3cbb6f01b6cc89414758fd898",
        "runtime_database": str(output / "runtime.db"),
        "components": summaries,
        "all_succeeded": all(item["status"] == "succeeded" for item in summaries),
        "truth_boundary": {
            "full_tcad_solver": False,
            "full_em_solver": False,
            "full_spice_solver": False,
            "commercial_eda": False,
            "runtime_authoritative": True
        },
    }
    (output / "acceptance.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload["all_succeeded"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
