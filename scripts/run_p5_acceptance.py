#!/usr/bin/env python3
"""Run AgenticPD proposal conversion and a fair two-run real ORFS comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "packages/contracts/src", ROOT / "packages/execution/src",
               ROOT / "packages/scheduler/src"):
    sys.path.insert(0, str(source))

from openroad_platform_contracts import ExperimentPlan, RuntimeStatus
from openroad_platform_execution import (PluginRegistry, ToolchainConfig,
    agenticpd_plugin_manifest, build_agenticpd_task, build_orfs_task, orfs_plugin_manifest)
from openroad_platform_scheduler import (
    CampaignManager, CampaignStore, RuntimeStore, WorkflowRuntime,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--rtl", type=Path, required=True)
    parser.add_argument("--runtime-db", type=Path)
    parser.add_argument("--campaign-db", type=Path)
    parser.add_argument("--orfs-root", type=Path, default=Path.home() / "OpenROAD-flow-scripts")
    parser.add_argument("--openroad-bin", type=Path, default=Path.home() / "bin/openroad")
    parser.add_argument("--yosys-bin", type=Path, default=Path.home() / "bin/yosys")
    parser.add_argument("--klayout-bin", type=Path, default=Path.home() / "bin/klayout")
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--max-parallel", type=int, default=1)
    args = parser.parse_args()
    out = args.output_root.resolve()
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(out)
    out.mkdir(parents=True, exist_ok=True)
    db = (args.runtime_db.expanduser().resolve() if args.runtime_db else
          Path("/tmp/openroad-platform-p5-runtime") / f"{out.name}.db")
    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        raise FileExistsError(db)
    toolchain = ToolchainConfig(name="orfs-2d-baseline", orfs_root=args.orfs_root.resolve(),
        openroad_bin=args.openroad_bin.resolve(), yosys_bin=args.yosys_bin.resolve(),
        klayout_bin=args.klayout_bin.resolve())
    registry = PluginRegistry([
        agenticpd_plugin_manifest(ROOT / ".external-src/agenticpd", default_timeout_seconds=600),
        orfs_plugin_manifest(toolchain, default_timeout_seconds=args.timeout),
    ])
    runtime = WorkflowRuntime(RuntimeStore(db), registry, workspace_root=out / "attempts",
                              worker_id="p5-real-acceptance", lease_seconds=60)
    started = time.monotonic()
    planner = runtime.submit(build_agenticpd_task(project_id="openroad-platform",
        design_id="p4_simple_adder", iterations=1, mode="mock", timeout_seconds=300,
        task_id="p5-agenticpd-proposal"))
    planner = runtime.execute_once(planner.run_id)
    if planner.status is not RuntimeStatus.SUCCEEDED:
        raise RuntimeError(runtime.describe(planner.run_id))
    plan_artifact = _artifact(runtime.describe(planner.run_id), "experiment_plan")
    plan = ExperimentPlan.from_dict(json.loads(plan_artifact.read_text(encoding="utf-8")))
    values = [plan.baseline_parameters["core_utilization_pct"],
              plan.candidates[0].parameters["core_utilization_pct"]]
    rtl_sha = _sha(args.rtl.resolve())
    tasks = []
    for role, utilization in zip(("baseline", "candidate"), values):
        tasks.append(build_orfs_task(
            args.rtl, project_id="openroad-platform", design_id="p5-adder",
            top="adder", platform_name="nangate45", target_stage="finish",
            clock_period_ns=10.0, core_utilization_pct=utilization, place_density=0.45,
            timeout_seconds=args.timeout, stage_timeout_seconds=min(args.timeout, 3600),
            task_id=f"p5-{role}", labels={"phase": "P5", "experiment_role": role,
                "plan_id": plan.plan_id}))
    campaign_db = (args.campaign_db.expanduser().resolve() if args.campaign_db else
                   db.with_suffix(".campaign.db"))
    campaign_store = CampaignStore(campaign_db)
    campaign_id = campaign_store.create(
        f"AgenticPD {plan.plan_id} baseline/candidate", tasks,
        max_parallel=args.max_parallel,
    )
    campaign = CampaignManager(campaign_store, runtime)
    campaign_view = campaign.run_until_terminal(
        campaign_id, timeout_seconds=args.timeout * len(tasks),
    )
    runs = []
    for role, utilization, member in zip(("baseline", "candidate"), values,
                                         campaign_view["members"]):
        run = runtime.store.get_run(member["run_id"])
        view = runtime.describe(run.run_id)
        report = json.loads(_artifact(view, "report").read_text(encoding="utf-8"))
        config = _artifact(view, "config").read_text(encoding="utf-8")
        runs.append({"role": role, "run_id": run.run_id, "status": run.status.value,
                     "requested_core_utilization_pct": utilization,
                     "parameter_consumed": f"CORE_UTILIZATION = {utilization:g}" in config,
                     "kpi": report["kpi"], "gds": _artifact_record(view, "gds")})
    comparable = (all(item["status"] == "succeeded" and item["parameter_consumed"] for item in runs)
                  and len({rtl_sha}) == 1)
    summary = {"schema_version": 1, "phase": "P5", "accepted": comparable,
        "seconds": round(time.monotonic() - started, 3), "proposal": plan.to_dict(),
        "proposal_qor_authoritative": False,
        "comparison": {"comparable": comparable, "same_rtl_sha256": rtl_sha,
                       "platform": "nangate45", "clock_period_ns": 10.0,
                       "place_density": 0.45, "runs": runs},
        "campaign": campaign_view,
        "real_llm": {"executed": False, "blocker": "no injected credential/paid budget"},
        "runtime_db": str(db), "campaign_db": str(campaign_store.path)}
    (out / "acceptance_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with sqlite3.connect(db) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        destination = out / "runtime.snapshot.db"
        destination.write_bytes(db.read_bytes())
    return 0 if comparable else 2


def _artifact(view: dict, kind: str) -> Path:
    matches = []
    for stage in view["stages"]:
        for attempt in stage["attempts"]:
            for item in attempt["artifacts"]:
                if item["kind"] == kind:
                    matches.append(Path(attempt["workspace"]) / item["store_key"])
    if matches:
        # ORFS registers both plan.json and analysis/report.json as reports.
        # The final analysis report is the only authoritative QoR source.
        return next((path for path in matches if path.as_posix().endswith("analysis/report.json")),
                    matches[-1])
    raise KeyError(kind)


def _artifact_record(view: dict, kind: str) -> dict:
    path = _artifact(view, kind)
    return {"size_bytes": path.stat().st_size, "sha256": _sha(path)}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
