#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Accumulate real observations into the live knowledge base (P1-4-A).

Runs small 2D synthesis campaigns (mux + adder, neighbor grids) through the
live stores and drives the same auto-learning hook the worker uses, so every
succeeded run lands in tenant-learning.db and every failure is recorded as a
rejection. Run with::

    python3 scripts/accumulate_observations.py --campaigns 2 --neighbors 6
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for source in (ROOT / "packages/contracts/src", ROOT / "packages/execution/src",
               ROOT / "packages/scheduler/src", ROOT / "packages/analysis/src",
               ROOT / "packages/visualization/src"):
    sys.path.insert(0, str(source))

from apps.api.app import ApiState  # noqa: E402

RTL_FIXTURES = {
    "mux_2to1": (ROOT / "tests/fixtures/p2_mux_2to1.v", "mux_2to1"),
}

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}


def pump_and_learn(state, run_id):
    """Execute a run to terminal, then auto-collect/reject (worker parity)."""
    run = state.runtime_store.get_run(run_id)
    while run.status.value not in TERMINAL:
        run = state.runtime.execute_once(run_id)
    return state.auto_collect_terminal_run(run_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaigns", type=int, default=2)
    parser.add_argument("--neighbors", type=int, default=6)
    parser.add_argument("--max-parallel", type=int, default=2)
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
    owner_id = "user-ea0c3d1f4520448d99a22d2dc7f7b250"  # yuanwenjie (already in live auth)
    before = len(state.tenant_learning_store.list(owner_id, "openroad-platform"))
    print("observations before:", before)

    fixture_names = list(RTL_FIXTURES)[:max(1, args.campaigns)]
    campaign_ids = []
    for name in fixture_names:
        rtl_path, top = RTL_FIXTURES[name]
        design = state.designs.import_rtl(
            filename=f"{name}.v", source=rtl_path.read_text(encoding="utf-8"),
            description=f"accumulate-{name}", owner_id=owner_id)
        design_id = design["id"]
        campaign = state.create_stage_campaign({
            "owner_id": owner_id, "design_id": design_id,
            "flow_mode": "campaign", "target_stage": "synth",
            "max_parallel": args.max_parallel,
            "stage_timeout_seconds": 300,
            "neighbor_count": args.neighbors,
        })
        campaign_ids.append(campaign["campaign_id"])
        print("campaign:", campaign["campaign_id"], "design:", name)

    # drive every campaign member to terminal with auto-learning
    summary = {"collect": 0, "reject": 0, "skipped": 0}
    for cid in campaign_ids:
        for member in state.stage_campaigns.store.members(cid):
            run = state.runtime_store.get_run(member.run_id) if member.run_id else None
            if run is None:
                run = state.runtime.submit(member.task_spec)
            result = pump_and_learn(state, run.run_id)
            action = result.get("action", "?")
            summary[action] = summary.get(action, 0) + 1
            print(f"  {member.task_spec.design_id} #{member.ordinal}: "
                  f"{run.status.value} -> {action} {result.get('status', '')}")

    after = len(state.tenant_learning_store.list(owner_id, "openroad-platform"))
    rejections = len(state.tenant_learning_store.rejections(owner_id, "openroad-platform"))
    print("summary:", json.dumps(summary))
    print("observations after:", after, "(+%d)" % (after - before))
    print("rejections:", rejections)
    assert after > before, "no observations accumulated"
    print("ACCUMULATE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
