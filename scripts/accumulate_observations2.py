#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1: accumulate real observations using ORFS typical designs.

For each design: create a stage-aware campaign over a neighbor parameter grid
(2D full flow to finish), drive it to completion with auto-learning so every
succeeded run lands in tenant-learning.db. Optionally runs 3D (TaiWei) cases.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for source in (ROOT / "packages/contracts/src", ROOT / "packages/execution/src",
               ROOT / "packages/scheduler/src", ROOT / "packages/analysis/src",
               ROOT / "packages/visualization/src"):
    sys.path.insert(0, str(source))

from apps.api.app import ApiState  # noqa: E402

OWNER = "user-ea0c3d1f4520448d99a22d2dc7f7b250"  # yuanwenjie (live)
PROJECT = "openroad-platform"
TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}


def pump_and_learn(state, run_id):
    run = state.runtime_store.get_run(run_id)
    while run.status.value not in TERMINAL:
        run = state.runtime.execute_once(run_id)
    return state.auto_collect_terminal_run(run_id)


def run_2d_batch(state, design_id, module, *, neighbors, max_parallel,
                 target_stage, budget_s):
    print(f"[2D] {module} ({design_id[:12]}) stage={target_stage} "
          f"neighbors={neighbors} ...", flush=True)
    campaign = state.create_stage_campaign({
        "owner_id": OWNER, "design_id": design_id,
        "flow_mode": "campaign", "target_stage": target_stage,
        "max_parallel": max_parallel, "stage_timeout_seconds": budget_s,
        "neighbor_count": neighbors,
    })
    cid = campaign["campaign_id"]
    for member in state.stage_campaigns.store.members(cid):
        run = state.runtime_store.get_run(member.run_id) if member.run_id else None
        if run is None:
            run = state.runtime.submit(member.task_spec)
        result = pump_and_learn(state, run.run_id)
        print(f"  #{member.ordinal} util="
              f"{member.task_spec.parameters.get('core_utilization_pct')} "
              f"{run.status.value} -> {result.get('action')} "
              f"{result.get('status', '')}", flush=True)
    return cid


def run_3d(state, design_id, module, tech):
    print(f"[3D] {module} {tech} ...", flush=True)
    state.ensure_taiwei_plugin()
    rtl_path = state.designs.rtl_path(design_id, owner_id=OWNER)
    from openroad_platform_execution import build_taiwei_task  # noqa: E402
    task = build_taiwei_task(
        project_id=PROJECT, design_id=module, tech=tech,
        registered_design_id=design_id, rtl={
            "path": str(rtl_path), "size_bytes": rtl_path.stat().st_size,
            "sha256": _sha256(rtl_path),
        },
        parameters={"core_utilization_pct": 45, "num_cores": 8},
        timeout_seconds=21600,
    )
    task.labels = {**task.labels, "owner_id": OWNER}
    run = state.runtime.submit(task)
    result = pump_and_learn(state, run.run_id)
    print(f"  {run.status.value} -> {result.get('action')} "
          f"{result.get('status', '')}", flush=True)


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--neighbors", type=int, default=6)
    parser.add_argument("--max-parallel", type=int, default=2)
    parser.add_argument("--target-stage", default="finish")
    parser.add_argument("--designs", default="gcd,ethmac")
    parser.add_argument("--stage-budget", type=int, default=3600)
    parser.add_argument("--with-3d", action="store_true")
    args = parser.parse_args()

    state = ApiState(
        ROOT / "var" / "platform.db", ROOT / "var" / "uploads",
        ROOT.parent / "OpenROAD-flow-scripts",
        design_root=ROOT / "var" / "designs", legacy_root=ROOT.parent / "iccad",
        runtime_db_path=ROOT / "var" / "public" / "runtime.db",
        campaign_db_path=ROOT / "var" / "public" / "campaign.db",
        optimization_db_path=ROOT / "var" / "public" / "optimization.db",
        auth_db_path=ROOT / "var" / "public" / "web-auth.db",
        byok_transport_secure=False, load_taiwei_plugin=False,
    )
    before = len(state.tenant_learning_store.list(OWNER, PROJECT))
    print("observations before:", before, flush=True)

    # map ORFS design name -> (design_id, module)
    orfs = {}
    for d in state.designs.list(limit=200):
        desc = str(d.get("description") or "")
        if desc.startswith("[ORFS]"):
            orfs[d["module"]] = d["id"]
    print("available ORFS designs:", sorted(orfs), flush=True)

    summary = {"collect": 0, "reject": 0}
    for name in [n.strip() for n in args.designs.split(",") if n.strip()]:
        design_id = orfs.get(name)
        if design_id is None:
            # fall back to module match
            match = [did for mod, did in orfs.items() if mod == name]
            design_id = match[0] if match else None
        if design_id is None:
            print(f"skip {name}: not an imported ORFS design", flush=True)
            continue
        cid = run_2d_batch(state, design_id, name, neighbors=args.neighbors,
                           max_parallel=args.max_parallel,
                           target_stage=args.target_stage,
                           budget_s=args.stage_budget)
        print(f"[2D] {name} campaign {cid} done", flush=True)
        if args.with_3d and name in ("gcd",):
            for tech in ("asap7_3D", "nangate45_3D"):
                run_3d(state, design_id, name, tech)

    after = len(state.tenant_learning_store.list(OWNER, PROJECT))
    rej = len(state.tenant_learning_store.rejections(OWNER, PROJECT))
    print(json.dumps({"observations": after, "delta": after - before,
                      "rejections": rej}))
    print("P1_BATCH_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
