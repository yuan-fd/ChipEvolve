#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify auto-learning against the real ApiState + real quick 2D runs.

Success path: a real ORFS synth run -> auto collect -> observation +1.
Failure path: an invalid RTL task -> auto reject -> rejection recorded, observations unchanged.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for source in (ROOT / "packages/contracts/src", ROOT / "packages/execution/src",
               ROOT / "packages/scheduler/src", ROOT / "packages/analysis/src",
               ROOT / "packages/visualization/src"):
    sys.path.insert(0, str(source))

from openroad_platform_execution import build_orfs_task  # noqa: E402
from apps.api.app import ApiState  # noqa: E402

TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}


def pump(state, run_id):
    run = state.runtime_store.get_run(run_id)
    while run.status.value not in TERMINAL:
        run = state.runtime.execute_once(run_id)
    return run


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="auto-learn-verify-"))
    state = ApiState(
        ROOT / "var" / "platform.db", ROOT / "var" / "uploads",
        ROOT.parent / "OpenROAD-flow-scripts",
        design_root=ROOT / "var" / "designs", legacy_root=ROOT.parent / "iccad",
        runtime_db_path=work / "runtime.db",
        optimization_db_path=work / "optimization.db",
 load_taiwei_plugin=False,
    )
    rtl = ROOT / "tests" / "fixtures" / "p2_mux_2to1.v"
    task = build_orfs_task(
        rtl, project_id="verify-auto", design_id="mux-verify", top="mux_2to1",
        target_stage="synth", stage_timeout_seconds=180, timeout_seconds=420,
        labels={"owner_id": "user-verify"},
    )
    run = state.runtime.submit(task)
    run = pump(state, run.run_id)
    result = state.auto_collect_terminal_run(run.run_id)
    print("success-run status:", run.status.value)
    print("auto result:", json.dumps(result, ensure_ascii=False))
    obs = state.tenant_learning_store.list("user-verify", "verify-auto")
    print("observations after success:", len(obs))
    assert run.status.value == "succeeded", run.status.value
    assert result["action"] == "collect", result

    # failing run: real RTL but wrong top module -> synthesis fails -> reject
    bad_task = build_orfs_task(
        rtl, project_id="verify-auto", design_id="mux-bad", top="nonexistent_top",
        target_stage="synth", stage_timeout_seconds=120, timeout_seconds=300,
        labels={"owner_id": "user-verify"},
    )
    run2 = state.runtime.submit(bad_task)
    run2 = pump(state, run2.run_id)
    result2 = state.auto_collect_terminal_run(run2.run_id)
    print("bad-run status:", run2.status.value)
    print("auto result2:", json.dumps(result2, ensure_ascii=False))
    rejections = state.tenant_learning_store.rejections("user-verify", "verify-auto")
    print("rejections:", len(rejections))
    obs2 = state.tenant_learning_store.list("user-verify", "verify-auto")
    print("observations after both:", len(obs2))
    assert result2["action"] == "reject", result2
    assert len(rejections) >= 1
    assert len(obs2) == len(obs), "observations must not grow on failed run"
    print("VERIFY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
