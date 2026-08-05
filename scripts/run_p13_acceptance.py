#!/usr/bin/env python3
"""Run a small real ORFS stage-aware parameter campaign."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "packages/contracts/src", ROOT / "packages/execution/src",
               ROOT / "packages/scheduler/src", ROOT / "packages/analysis/src",
               ROOT / "packages/visualization/src"):
    sys.path.insert(0, str(source))

from openroad_platform_execution import (  # noqa: E402
    PluginRegistry, ToolchainConfig, build_orfs_task, orfs_plugin_manifest,
)
from openroad_platform_scheduler import (  # noqa: E402
    CampaignStore, RuntimeStore, StageAwareCampaignManager, WorkflowRuntime,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_root.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(output)
    output.mkdir(parents=True, exist_ok=True)
    private = Path("/tmp") / f"openroad-platform-p13-{uuid.uuid4().hex}"
    toolchain = ToolchainConfig.from_environment(
        name="p13-real", orfs_root=ROOT.parent / "OpenROAD-flow-scripts",
        openroad_bin=ROOT.parent / "bin/openroad", yosys_bin=ROOT.parent / "bin/yosys",
        klayout_bin=ROOT.parent / "bin/klayout",
    )
    runtime = WorkflowRuntime(
        RuntimeStore(private / "runtime.db"),
        PluginRegistry([orfs_plugin_manifest(toolchain)]),
        workspace_root=output / "runtime-workspaces", worker_id="p13-acceptance",
    )
    manager = StageAwareCampaignManager(CampaignStore(private / "campaign.db"), runtime)
    base = build_orfs_task(
        ROOT / "tests/fixtures/p2_mux_2to1.v", project_id="p13-real",
        design_id="mux-2to1", top="mux_2to1", target_stage="synth",
        stage_timeout_seconds=120, timeout_seconds=300,
    )
    campaign_id = manager.create_grid(
        "p13-real-synth-grid", base, {"core_utilization_pct": [10, 20]},
        max_parallel=2, stage_budgets={"synth": 90}, max_repairs=1,
        max_total_runs=4,
    )
    view = manager.run_until_terminal(campaign_id, timeout_seconds=240)
    _write(output / "campaign_snapshot.json", view)
    if view["counts"] != {"succeeded": 2}:
        raise RuntimeError(f"Unexpected P13 campaign result: {view['counts']}")
    event_counts = {}
    for member in view["members"]:
        events = runtime.store.events(member["run_id"])
        event_counts[member["run_id"]] = sum(
            event["event_type"].startswith("tool.stage.") for event in events
        )
    if set(event_counts.values()) != {2}:
        raise RuntimeError(f"Real stage events are incomplete: {event_counts}")
    summary = {
        "schema_version": 1, "phase": "P13", "accepted": True,
        "campaign_id": campaign_id, "status": view["status"],
        "candidate_count": 2, "max_parallel": 2,
        "parameter_grid": {"core_utilization_pct": [10, 20]},
        "target_stage": "synth", "stage_budgets": {"synth": 90},
        "tool_stage_event_counts": event_counts,
        "runtime_authoritative": True, "pruning_policy": "stage_wall_clock_v1",
        "repair_policy": "limited-react-v1", "unit_verified_top_k": True,
        "unit_verified_pruning": True, "unit_verified_repair_child": True,
    }
    _write(output / "acceptance_summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def _write(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
