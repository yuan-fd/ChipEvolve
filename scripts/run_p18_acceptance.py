#!/usr/bin/env python3
"""Run P18 bounded-real EDACraft capabilities through Workflow Runtime."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for relative in ("packages/contracts/src", "packages/execution/src", "packages/scheduler/src"):
    sys.path.insert(0, str(ROOT / relative))

from openroad_platform_execution import (  # noqa: E402
    PluginRegistry, build_edacraft_task, edacraft_component, edacraft_plugin_manifest,
)
from openroad_platform_scheduler import RuntimeStore, WorkflowRuntime  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_root.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = ROOT / ".external-src" / "edacraft"
    slugs = ("rtlcraft", "edacode", "tcadcraft", "momcraft", "cktcraft")
    manifests = [edacraft_plugin_manifest(slug, source, Path(sys.executable)) for slug in slugs]
    runtime = WorkflowRuntime(
        RuntimeStore(output / "runtime.db"), PluginRegistry(manifests),
        workspace_root=output / "workspaces", worker_id="p18-acceptance", lease_seconds=30,
    )
    records = []
    for slug in slugs:
        component = edacraft_component(slug)
        run = runtime.submit(build_edacraft_task(slug, task_id=f"p18-{slug}"),
                             capability=component.capability)
        runtime.execute_once(run.run_id)
        detail = runtime.describe(run.run_id)
        attempt = detail["stages"][0]["attempts"][-1]
        records.append({
            "component": component.name, "run_id": run.run_id,
            "status": detail["run"]["status"], "capability": component.capability,
            "artifacts": [{key: item[key] for key in ("kind", "store_key", "sha256", "size_bytes")}
                          for item in attempt["artifacts"]],
            "metrics": attempt["metrics"], "events": len(detail["events"]),
        })
    accepted = all(item["status"] == "succeeded" for item in records)
    payload = {
        "schema_version": 1, "phase": "P18", "accepted": accepted,
        "source_commit": "739eee0f3ced8fc3cbb6f01b6cc89414758fd898",
        "runtime_authoritative": True, "components": records,
        "truth_boundary": {
            "cktcraft": "real bounded DC operating-point solve",
            "momcraft": "real one-frequency coarse numerical microstrip solve",
            "tcadcraft": "geometry and physics invariants; full solver build upstream-blocked",
            "edacode": "proposal-only; no shell or file-write tools",
            "signoff_claimed": False,
        },
    }
    (output / "acceptance.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
