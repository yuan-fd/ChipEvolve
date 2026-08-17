#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify batch-parallel (L1) mode end-to-end with auto neighbor candidates."""
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

from apps.api.app import ApiState  # noqa: E402

RTL = r"""module mux_2to1 (a, b, s, y);
  input a, b, s;
  output y;
  assign y = s ? b : a;
endmodule
"""


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="l1-batch-verify-"))
    state = ApiState(
        ROOT / "var" / "platform.db", ROOT / "var" / "uploads",
        ROOT.parent / "OpenROAD-flow-scripts",
        design_root=work / "designs", legacy_root=ROOT.parent / "iccad",
        runtime_db_path=work / "runtime.db", campaign_db_path=work / "campaign.db",
        optimization_db_path=work / "optimization.db",
        byok_transport_secure=False, load_taiwei_plugin=False,
    )
    session, _ = state.auth.register("l1-verify-user", "l1-verify-pass-1")
    owner_id = session.user_id
    print("owner:", owner_id)
    design = state.designs.import_rtl(
        filename="mux.v", source=RTL, description="L1 verify", owner_id=owner_id)
    design_id = design["id"]
    print("design:", design_id, "module:", design.get("module"))

    # one-click batch: NO hand-built grid -> auto neighbor candidates (default 6)
    campaign = state.create_stage_campaign({
        "owner_id": owner_id, "design_id": design_id,
        "flow_mode": "campaign", "target_stage": "synth",
        "max_parallel": 2, "stage_timeout_seconds": 180,
        "neighbor_count": 6,
    })
    cid = campaign["campaign_id"]
    members = state.stage_campaigns.store.members(cid)
    print("campaign:", cid, "members:", len(members))
    assert len(members) == 6, f"expected 6 neighbor candidates, got {len(members)}"
    utils = sorted({m.task_spec.parameters.get("core_utilization_pct")
                    for m in members})
    print("candidate utilizations:", utils)
    assert len(utils) >= 3, "neighbor candidates not spread around baseline"

    view = state.stage_campaigns.run_until_terminal(cid, timeout_seconds=900)
    print("campaign status:", view.get("status"))
    counts = view.get("counts") or {}
    print("counts:", json.dumps(counts))
    assert counts.get("succeeded", 0) >= 1, "batch run produced no succeeded member"

    # results table
    rows = []
    for m in members:
        run = state.runtime_store.get_run(m.run_id) if m.run_id else None
        rows.append({"ordinal": m.ordinal,
                     "utilization": m.task_spec.parameters.get("core_utilization_pct"),
                     "status": run.status.value if run else "?"})
    print("results:", json.dumps(rows, ensure_ascii=False))
    print("L1_VERIFY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
