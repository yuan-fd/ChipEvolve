#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1: fast top-up — 3D missing combos + 2D finish with varied clock periods."""
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


def pump_and_learn(state, run_id):
    run = state.runtime_store.get_run(run_id)
    while run.status.value not in TERMINAL:
        run = state.runtime.execute_once(run_id)
    return state.auto_collect_terminal_run(run_id)


def run_3d(state, design_id, module, tech):
    print(f"[3D] {module} {tech} ...", flush=True)
    state.ensure_taiwei_plugin()
    rtl_path = state.designs.rtl_path(design_id, owner_id=OWNER)
    from openroad_platform_execution import build_taiwei_task  # noqa: E402
    task = build_taiwei_task(
        project_id=PROJECT, design_id=module, tech=tech,
        registered_design_id=design_id,
        rtl={"path": str(rtl_path), "size_bytes": rtl_path.stat().st_size,
             "sha256": _sha256(rtl_path)},
        parameters={"core_utilization_pct": 45, "num_cores": 8},
        timeout_seconds=21600,
    )
    task = dataclasses.replace(task, labels={**task.labels, "owner_id": OWNER})
    run = state.runtime.submit(task)
    result = pump_and_learn(state, run.run_id)
    print(f"  {run.status.value} -> {result.get('action')} {result.get('status', '')}",
          flush=True)


def run_2d(state, design_id, module, *, clock_ns, neighbors, max_parallel, budget_s):
    print(f"[2D] {module} clock={clock_ns}ns neighbors={neighbors} ...", flush=True)
    campaign = state.create_stage_campaign({
        "owner_id": OWNER, "design_id": design_id, "flow_mode": "campaign",
        "target_stage": "finish", "max_parallel": max_parallel,
        "stage_timeout_seconds": budget_s, "neighbor_count": neighbors,
        "clock_period_ns": clock_ns,
    })
    cid = campaign["campaign_id"]
    for member in state.stage_campaigns.store.members(cid):
        run = state.runtime_store.get_run(member.run_id) if member.run_id else None
        if run is None:
            run = state.runtime.submit(member.task_spec)
        result = pump_and_learn(state, run.run_id)
        print(f"  #{member.ordinal} {run.status.value} -> {result.get('action')} "
              f"{result.get('status', '')}", flush=True)
    return cid


def main() -> int:
    state = ApiState(
        ROOT / "var" / "platform.db", ROOT / "var" / "uploads",
        ROOT.parent / "OpenROAD-flow-scripts",
        design_root=ROOT / "var" / "designs", legacy_root=ROOT.parent / "iccad",
        runtime_db_path=ROOT / "var" / "public" / "runtime.db",
        optimization_db_path=ROOT / "var" / "public" / "optimization.db",
        auth_db_path=ROOT / "var" / "public" / "web-auth.db",
 load_taiwei_plugin=False,
    )
    before = len(state.tenant_learning_store.list(OWNER, PROJECT))
    print("observations before:", before, flush=True)
    orfs = {d["module"]: d["id"] for d in state.designs.list(limit=200)
            if str(d.get("description") or "").startswith("[ORFS]")}

    # 1) 3D missing combos: aes/ethmac on nangate45_3D + asap7_nangate45_3D
    for name in ("aes_cipher_top", "ethmac"):
        for tech in ("nangate45_3D", "asap7_nangate45_3D"):
            run_3d(state, orfs[name], name, tech)

    # 2) 2D finish top-up with different clock periods (new fingerprints)
    run_2d(state, orfs["gcd"], "gcd", clock_ns=8, neighbors=6,
           max_parallel=3, budget_s=3600)
    run_2d(state, orfs["ethmac"], "ethmac", clock_ns=8, neighbors=6,
           max_parallel=3, budget_s=3600)
    run_2d(state, orfs["gcd"], "gcd", clock_ns=12, neighbors=6,
           max_parallel=3, budget_s=3600)

    after = len(state.tenant_learning_store.list(OWNER, PROJECT))
    print(json.dumps({"observations": after, "delta": after - before,
                      "rejections": len(state.tenant_learning_store.rejections(OWNER, PROJECT))}))
    print("P1_TOPDONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
