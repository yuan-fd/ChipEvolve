#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1 final: gcd-only top-up (fast under load) — 2D finish with varied clocks + gcd 3D."""
import concurrent.futures
import dataclasses
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

OWNER = "user-ea0c3d1f4520448d99a22d2dc7f7b250"
PROJECT = "openroad-platform"
TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def pump_one(state, run_id):
    run = state.runtime_store.get_run(run_id)
    while run.status.value not in TERMINAL:
        run = state.runtime.execute_once(run_id)
    result = state.auto_collect_terminal_run(run_id)
    return run.status.value, result


def run_2d_parallel(state, design_id, module, *, clock_ns, neighbors, workers):
    print(f"[2D] {module} clock={clock_ns}ns neighbors={neighbors} "
          f"workers={workers} ...", flush=True)
    campaign = state.create_stage_campaign({
        "owner_id": OWNER, "design_id": design_id, "flow_mode": "campaign",
        "target_stage": "finish", "max_parallel": workers,
        "stage_timeout_seconds": 3600, "neighbor_count": neighbors,
        "clock_period_ns": clock_ns,
    })
    cid = campaign["campaign_id"]
    members = state.stage_campaigns.store.members(cid)
    run_ids = [(m.ordinal, state.runtime.submit(m.task_spec).run_id,
                m.task_spec.parameters.get("core_utilization_pct"))
               for m in members]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(pump_one, state, rid): (ordinal, util)
                   for ordinal, rid, util in run_ids}
        for fut in concurrent.futures.as_completed(futures):
            ordinal, util = futures[fut]
            status, result = fut.result()
            print(f"  #{ordinal} util={util} {status} -> "
                  f"{result.get('action')} {result.get('status', '')}", flush=True)
    return cid


def run_3d(state, design_id, module, tech, util=45):
    print(f"[3D] {module} {tech} util={util} ...", flush=True)
    state.ensure_taiwei_plugin()
    rtl_path = state.designs.rtl_path(design_id, owner_id=OWNER)
    from openroad_platform_execution import build_taiwei_task  # noqa: E402
    task = build_taiwei_task(
        project_id=PROJECT, design_id=module, tech=tech,
        registered_design_id=design_id,
        rtl={"path": str(rtl_path), "size_bytes": rtl_path.stat().st_size,
             "sha256": _sha256(rtl_path)},
        parameters={"core_utilization_pct": util, "num_cores": 8},
        timeout_seconds=21600,
    )
    task = dataclasses.replace(task, labels={**task.labels, "owner_id": OWNER})
    run = state.runtime.submit(task)
    status, result = pump_one(state, run.run_id)
    print(f"  {status} -> {result.get('action')} {result.get('status', '')}", flush=True)


def main() -> int:
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
    orfs = {d["module"]: d["id"] for d in state.designs.list(limit=200)
            if str(d.get("description") or "").startswith("[ORFS]")}

    # gcd 2D finish: clock 12ns, 6ns, 7ns (new fingerprints; skip existing)
    run_2d_parallel(state, orfs["gcd"], "gcd", clock_ns=12, neighbors=6, workers=3)
    run_2d_parallel(state, orfs["gcd"], "gcd", clock_ns=6, neighbors=6, workers=3)
    run_2d_parallel(state, orfs["gcd"], "gcd", clock_ns=7, neighbors=6, workers=3)

    # gcd 3D extra params
    for util in (30, 60):
        run_3d(state, orfs["gcd"], "gcd", "asap7_3D", util=util)
    for tech in ("nangate45_3D", "asap7_nangate45_3D"):
        run_3d(state, orfs["gcd"], "gcd", tech, util=55)

    after = len(state.tenant_learning_store.list(OWNER, PROJECT))
    print(json.dumps({"observations": after, "delta": after - before,
                      "rejections": len(state.tenant_learning_store.rejections(OWNER, PROJECT))}))
    print("P1_FINAL_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
