#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quick 3D #10: gcd asap7_3D util=65 (fast platform)."""
import dataclasses, json, sys
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
    orfs = {d["module"]: d["id"] for d in state.designs.list(limit=200)
            if str(d.get("description") or "").startswith("[ORFS]")}
    state.ensure_taiwei_plugin()
    from openroad_platform_execution import build_taiwei_task  # noqa: E402
    rtl_path = state.designs.rtl_path(orfs["gcd"], owner_id=OWNER)
    task = build_taiwei_task(
        project_id=PROJECT, design_id="gcd", tech="asap7_3D",
        registered_design_id=orfs["gcd"],
        rtl={"path": str(rtl_path), "size_bytes": rtl_path.stat().st_size,
             "sha256": _sha256(rtl_path)},
        parameters={"core_utilization_pct": 65, "num_cores": 8},
        timeout_seconds=21600,
    )
    task = dataclasses.replace(task, labels={**task.labels, "owner_id": OWNER})
    run = state.runtime.submit(task)
    while run.status.value not in TERMINAL:
        run = state.runtime.execute_once(run.run_id)
    result = state.auto_collect_terminal_run(run.run_id)
    print(f"[3D] gcd asap7_3D util=65 {run.status.value} -> "
          f"{result.get('action')} {result.get('status', '')}", flush=True)
    after = len(state.tenant_learning_store.list(OWNER, PROJECT))
    print(json.dumps({"obs": after, "delta": after - before}))
    print("P1_3D_LAST_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
