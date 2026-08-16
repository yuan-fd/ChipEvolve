from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from apps.api.app import ApiState
from openroad_platform_contracts import RuntimeStatus, TaskSpec


ROOT = Path(__file__).resolve().parents[1]

GCD_RTL = (
    "module gcd(input clk, input reset, input start, input [31:0] a_in, "
    "input [31:0] b_in, output reg [31:0] result, output reg done);\n"
    "  reg [31:0] a, b;\n"
    "  always @(posedge clk) begin\n"
    "    if (reset) begin a <= 0; b <= 0; result <= 0; done <= 0; end\n"
    "    else if (start) begin a <= a_in; b <= b_in; done <= 0; end\n"
    "    else if (!done) begin\n"
    "      if (a == 0) begin result <= b; done <= 1; end\n"
    "      else if (b == 0) begin result <= a; done <= 1; end\n"
    "      else if (a > b) a <= a - b;\n"
    "      else b <= b - a;\n"
    "    end\n"
    "  end\n"
    "endmodule\n"
)
MUX4_RTL = (
    "module mux4 #(parameter W=8)(input [W-1:0] a,b,c,d, input [1:0] sel, "
    "output reg [W-1:0] y);\n"
    "  always @* case(sel) 2'd0:y=a; 2'd1:y=b; 2'd2:y=c; default:y=d; endcase\n"
    "endmodule\n"
)


def make_state(tmp_path: Path) -> ApiState:
    return ApiState(
        tmp_path / "platform.db",
        tmp_path / "uploads",
        tmp_path / "orfs",
        design_root=tmp_path / "designs",
        legacy_root=tmp_path / "legacy",
        yosys_bin=ROOT.parent / "bin" / "yosys",
        runtime_db_path=tmp_path / "runtime.db",
        campaign_db_path=tmp_path / "campaign.db",
        load_taiwei_plugin=True,
    )


def succeed_orfs_baseline(state: ApiState, design_id: str, *,
                          workspace: Path, owner_id: str = "") -> str:
    task = TaskSpec(
        task_id="taiwei-2d-baseline", project_id="openroad-platform",
        design_id=design_id, plugin_id="orfs",
        labels={"owner_id": owner_id},
    )
    run, stage = state.runtime_store.submit_plugin_run(task, plugin_version="1.0.0")
    workspace.mkdir(parents=True, exist_ok=True)
    attempt = state.runtime_store.start_attempt(
        stage.stage_run_id, worker_id="test", workspace=str(workspace),
        lease_seconds=30,
    )
    state.runtime_store.finish_attempt(
        attempt.attempt_id, RuntimeStatus.SUCCEEDED, exit_code=0,
        now=datetime.now(timezone.utc),
    )
    return run.run_id


def test_submit_taiwei_design_run_accepts_non_gcd_design(tmp_path):
    state = make_state(tmp_path)
    assert state.taiwei_readiness["ready"] is True
    mux4 = state.designs.import_rtl(filename="mux4.v", source=MUX4_RTL)

    result = state.submit_taiwei_design_run({
        "design_id": mux4["id"], "clock": "clk", "clock_period_ns": 8.0,
        "tech": "nangate45_3D", "core_utilization_pct": 55,
        "num_cores": 4, "cts_layer": "upper", "outer_iterations": 2,
        "skip_2d_part": False, "pin3d_allow_net_flow": True,
        "pin3d_split_net_flow": True, "abc_area": True,
    })

    # Generalised adapter: any registered design submits a normal 3D run with
    # its RTL reference and engine-native parameters carried through.
    assert "guidance_required" not in str(result.get("status"))
    run = result["run"]
    assert run["task_spec"]["plugin_id"] == "taiwei-pin-3d"
    assert run["task_spec"]["inputs"]["case"] == "mux4"
    assert run["task_spec"]["inputs"]["tech"] == "nangate45_3D"
    assert run["task_spec"]["inputs"]["rtl"]["path"]
    assert run["task_spec"]["inputs"]["clock"] == "clk"
    assert run["task_spec"]["inputs"]["clock_period_ns"] == 8.0
    assert run["task_spec"]["parameters"] == {
        "core_utilization_pct": 55, "num_cores": 4, "cts_layer": "upper",
        "outer_iterations": 2, "skip_2d_part": False,
        "pin3d_allow_net_flow": True, "pin3d_split_net_flow": True,
        "abc_area": True,
    }


def test_submit_taiwei_design_run_rejects_unknown_3d_platform(tmp_path):
    state = make_state(tmp_path)
    gcd = state.designs.import_rtl(filename="gcd.v", source=GCD_RTL)

    with pytest.raises(ValueError, match="Unsupported TaiWei 3D platform"):
        state.submit_taiwei_design_run({
            "design_id": gcd["id"], "tech": "nangate45",
        })


def test_submit_taiwei_design_run_submits_without_baseline(tmp_path):
    state = make_state(tmp_path)
    gcd = state.designs.import_rtl(filename="gcd.v", source=GCD_RTL)

    result = state.submit_taiwei_design_run({"design_id": gcd["id"]})

    # 2D baseline is optional: the 3D flow runs standalone.
    assert "guidance_required" not in str(result.get("status"))
    run = result["run"]
    assert run["task_spec"]["plugin_id"] == "taiwei-pin-3d"
    assert "baseline_run_id" not in run["task_spec"]["labels"]


def test_submit_taiwei_design_run_returns_guidance_for_non_succeeded_baseline(tmp_path):
    state = make_state(tmp_path)
    gcd = state.designs.import_rtl(filename="gcd.v", source=GCD_RTL)
    task = TaskSpec(
        task_id="taiwei-2d-failed", project_id="openroad-platform",
        design_id=gcd["id"], plugin_id="orfs",
    )
    run, _ = state.runtime_store.submit_plugin_run(task, plugin_version="1.0.0")

    guidance = state.submit_taiwei_design_run({
        "design_id": gcd["id"], "baseline_run_id": run.run_id,
    })

    assert guidance["status"] == "guidance_required"
    assert guidance["reason"] == "baseline_invalid"


def test_submit_taiwei_design_run_submits_with_optional_baseline(tmp_path):
    state = make_state(tmp_path)
    gcd = state.designs.import_rtl(filename="gcd.v", source=GCD_RTL)
    baseline_run_id = succeed_orfs_baseline(
        state, gcd["id"], workspace=tmp_path / "baseline-ws"
    )

    result = state.submit_taiwei_design_run({
        "design_id": gcd["id"], "baseline_run_id": baseline_run_id,
    })

    # Baseline is associated when present; submission stays normal.
    assert "guidance_required" not in str(result.get("status"))
    run = result["run"]
    assert run["run_id"]
    assert run["task_spec"]["plugin_id"] == "taiwei-pin-3d"
    assert run["task_spec"]["labels"]["baseline_run_id"] == baseline_run_id
    assert run["task_spec"]["inputs"]["case"] == "gcd"
    assert run["task_spec"]["inputs"]["tech"] == "asap7_3D"


def test_frontend_renders_taiwei_guided_flow_and_support_scope():
    html = (ROOT / "apps/web/index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "apps/web/assets/app.js").read_text(encoding="utf-8")

    # Generalised 3D branch: configurable platform and engine knobs rendered.
    assert "taiwei-3d" in html or "taiwei-3d" in javascript
    assert "taiwei3dConfigForm" in javascript
    assert "taiweiTech" in javascript
    assert "taiweiUtil" in javascript
    assert "taiweiCtsLayer" in javascript
    assert "Generate 3D" in javascript
    assert "guidance_required" in javascript
    assert "message_zh" in javascript
    submit = javascript.split("async function submitTaiweiExtension", 1)[1].split(
        "async function", 1
    )[0]
    for field in (
        "tech", "clock", "clock_period_ns", "core_utilization_pct", "num_cores",
        "cts_layer", "outer_iterations", "skip_2d_part",
        "pin3d_split_net_flow", "pin3d_allow_net_flow", "abc_area",
    ):
        assert f"payload.{field}" in submit
    combined = html.lower() + javascript.lower()
    assert "gcd-only" not in combined and "gcd only" not in combined
    assert "complete one successful 2d baseline" not in combined
