from __future__ import annotations

import json
from pathlib import Path

import pytest

from openroad_platform_scheduler import (
    CodexCliSpecProvider,
    RuleBasedSpecProvider,
    SpecConversationManager,
    SpecConversationStore,
    SpecProposal,
)


def test_registered_design_reaches_confirmed_deterministic_task(tmp_path):
    rtl = tmp_path / "top.v"
    rtl.write_text("module top(input clk, input a, output y); assign y = a; endmodule\n")
    store = SpecConversationStore(tmp_path / "spec.db")
    manager = SpecConversationManager(store, RuleBasedSpecProvider())

    session = manager.create(
        message="把这个设计用 OpenROAD 跑到 route，时钟 clk，周期 5ns，利用率 30%",
        design_id="design-1", design_context={"module": "top"},
    )

    assert session["status"] == "ready"
    assert session["state"]["ready_for_execution"] is True
    assert [turn["role"] for turn in session["turns"]] == ["user", "assistant"]
    with pytest.raises(ValueError, match="confirmation"):
        manager.compile(session["session_id"], rtl_path=rtl,
                        design_id="design-1", confirmed=False)
    task = manager.compile(session["session_id"], rtl_path=rtl,
                           design_id="design-1", confirmed=True)
    assert task.plugin_id == "orfs"
    assert task.parameters["target_stage"] == "route"
    assert task.parameters["clock_period_ns"] == 5.0
    assert task.parameters["core_utilization_pct"] == 30.0
    assert task.inputs["clock"] == "clk"
    assert task.labels["spec_session_id"] == session["session_id"]


def test_missing_design_forces_clarification_and_budgets_are_enforced(tmp_path):
    manager = SpecConversationManager(
        SpecConversationStore(tmp_path / "spec.db"), RuleBasedSpecProvider()
    )
    session = manager.create(message="做一个芯片并生成 GDS", budgets={"max_turns": 1})
    assert session["status"] == "clarification_required"
    assert "rtl_or_design" in session["state"]["missing_fields"]
    with pytest.raises(ValueError, match="turn budget"):
        manager.turn(session["session_id"], "顶层是 top")


def test_codex_provider_is_ephemeral_read_only_and_schema_validated(tmp_path, monkeypatch):
    output = {
        "objective": "实现二输入与门并生成 GDS", "functionality": "y=a&b",
        "top": "and2", "clock": None, "reset": None,
        "target_platform": "nangate45", "target_stage": "finish",
        "clock_period_ns": 10.0, "core_utilization_pct": 10.0,
        "place_density": 0.45,
        "rtl_source": "module and2(input a,input b,output y); assign y=a&b; endmodule",
        "missing_fields": [], "assumptions": ["combinational"],
        "clarification_questions": [], "ready_for_execution": True,
    }
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["cwd"] = Path(kwargs["cwd"])
        Path(command[command.index("--output-last-message") + 1]).write_text(
            json.dumps(output), encoding="utf-8"
        )
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("openroad_platform_scheduler.spec_conversation.subprocess.run", fake_run)
    provider = CodexCliSpecProvider(model="gpt-5.6-sol", executable="/usr/bin/codex")
    proposal = provider.propose([{"role": "user", "content": "and gate"}], {})

    assert proposal.ready_for_execution is True
    assert proposal.top == "and2"
    assert "--ephemeral" in captured["command"]
    assert captured["command"][captured["command"].index("--sandbox") + 1] == "read-only"
    assert captured["command"][captured["command"].index("--model") + 1] == "gpt-5.6-sol"


def test_provider_rejects_unsafe_generated_rtl():
    with pytest.raises(ValueError, match="forbidden"):
        SpecProposal.from_mapping({
            "objective": "bad", "functionality": "bad", "top": "top",
            "target_platform": "nangate45", "target_stage": "finish",
            "clock_period_ns": 10, "core_utilization_pct": 10,
            "place_density": 0.45,
            "rtl_source": "module top; initial $system(\"sh\"); endmodule",
            "missing_fields": [], "assumptions": [],
            "clarification_questions": [], "ready_for_execution": True,
        })
