from pathlib import Path
from shutil import which

import pytest

from apps.api.app import ApiState
from openroad_platform_contracts import TaskSpec


def make_state(tmp_path: Path) -> ApiState:
    return ApiState(
        tmp_path / "platform.db",
        tmp_path / "uploads",
        tmp_path / "orfs",
        design_root=tmp_path / "designs",
        legacy_root=tmp_path / "legacy",
        yosys_bin=Path("/missing/yosys"),
        runtime_db_path=tmp_path / "runtime.db",
        campaign_db_path=tmp_path / "campaign.db",
    )


def test_web_submission_is_persisted_and_can_be_cancelled(tmp_path):
    state = make_state(tmp_path)

    job = state.submit_run({
        "filename": "counter.v",
        "rtl_source": "module counter(input clk, output reg q); always @(posedge clk) q <= ~q; endmodule\n",
        "top": "counter",
        "clock": "clk",
        "target_stage": "route",
    })

    assert job["status"] == "queued"
    assert Path(job["request"]["rtl_path"]).read_text().startswith("module counter")
    assert state.get_run(job["id"])["events"][0]["kind"] == "submitted"
    assert state.cancel_run(job["id"])["status"] == "cancelled"


@pytest.mark.parametrize("filename", ["../escape.v", "design.txt", "bad name.v"])
def test_web_submission_rejects_unsafe_filename(tmp_path, filename):
    state = make_state(tmp_path)

    with pytest.raises(ValueError, match="filename"):
        state.submit_run({"filename": filename, "rtl_source": "module top; endmodule"})


def test_health_distinguishes_web_and_execution_readiness(tmp_path):
    state = make_state(tmp_path)

    health = state.health()

    assert health["ok"] is True
    assert health["database_ready"] is True
    assert health["orfs_ready"] is False
    assert health["execution_ready"] is False


def test_runtime_and_campaign_queries_use_authoritative_store(tmp_path):
    state = make_state(tmp_path)
    task = TaskSpec(task_id="p6-api-task", project_id="p6", design_id="api",
                    plugin_id="echo")
    run, _ = state.runtime_store.submit_plugin_run(task, plugin_version="1.0.0")
    campaign_id = state.campaign_store.create("api-campaign", [task])
    member = state.campaign_store.members(campaign_id)[0]
    state.campaign_store.bind(member.member_id, run.run_id)

    assert state.list_runtime_runs()["runs"][0]["run_id"] == run.run_id
    campaign = state.get_campaign(campaign_id)
    assert campaign["members"][0]["status"] == "queued"
    cancelled = state.cancel_campaign(campaign_id)
    assert cancelled["members"][0]["status"] == "cancelled"


def test_design_import_creates_netlist_schematic_and_analysis(tmp_path):
    yosys = which("yosys")
    if yosys is None:
        pytest.skip("Yosys is not installed")
    state = ApiState(
        tmp_path / "platform.db",
        tmp_path / "uploads",
        tmp_path / "orfs",
        design_root=tmp_path / "designs",
        legacy_root=tmp_path / "legacy",
        yosys_bin=Path(yosys),
        runtime_db_path=tmp_path / "runtime.db",
        campaign_db_path=tmp_path / "campaign.db",
    )

    design = state.designs.import_rtl(
        filename="xor_gate.v",
        source="module xor_gate(input a, input b, output y); assign y = a ^ b; endmodule\n",
    )

    detail = state.designs.get(design["id"], include_source=True)
    assert detail["module"] == "xor_gate"
    assert detail["analysis"]["instance_count"] > 0
    assert "module xor_gate" in detail["rtl_source"]
    assert "<svg" in state.designs.schematic(design["id"])
