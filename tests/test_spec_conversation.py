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
    store = SpecConversationStore(tmp_path / "spec.db")
    manager = SpecConversationManager(store, RuleBasedSpecProvider())

    session = manager.create(
        message="把这个设计用 OpenROAD 跑到 route，时钟 clk，周期 5ns，利用率 30%",
        design_id="design-1", design_context={"module": "top", "analysis": {
            "inputs": ["clk", "a"], "outputs": ["y"],
        }},
    )

    assert session["status"] == "ready"
    assert session["state"]["ready_for_execution"] is True
    assert [turn["role"] for turn in session["turns"]] == ["user", "assistant"]
    assert [port.name for port in SpecProposal.from_mapping(session["state"]).ports] == ["clk", "a", "y"]
    with pytest.raises(RuntimeError, match="removed in v2"):
        manager.compile(session["session_id"], rtl_path=tmp_path / "top.v",
                        design_id="design-1", confirmed=True)


def test_missing_design_forces_clarification_and_budgets_are_enforced(tmp_path):
    manager = SpecConversationManager(
        SpecConversationStore(tmp_path / "spec.db"), RuleBasedSpecProvider()
    )
    session = manager.create(message="做一个芯片并生成 GDS", budgets={"max_turns": 1})
    assert session["status"] == "clarification_required"
    assert set(session["state"]["missing_fields"]) == {"top", "ports"}
    with pytest.raises(ValueError, match="turn budget"):
        manager.turn(session["session_id"], "顶层是 top")


def test_spec_ir_preserves_supported_orfs_process_request(tmp_path):
    manager = SpecConversationManager(SpecConversationStore(tmp_path / "spec.db"),
                                      RuleBasedSpecProvider())
    session = manager.create(
        message="顶层为 divider，使用 sky130hd 跑完整 GDS，端口 clk 输入和 tick 输出",
    )
    assert session["state"]["target_platform"] == "sky130hd"
    assert "sky130hd" in session["state"]["assumptions"][0]


def test_codex_provider_is_ephemeral_read_only_and_schema_validated(tmp_path, monkeypatch):
    output = {
        "objective": "实现二输入与门并生成 GDS", "functionality": "y=a&b",
        "top": "and2", "clock": None, "reset": None,
        "target_platform": "nangate45", "target_stage": "finish",
        "clock_period_ns": 10.0, "core_utilization_pct": 10.0,
        "place_density": 0.45,
        "ports": [{"name": "a", "direction": "input", "width": 1},
                  {"name": "b", "direction": "input", "width": 1},
                  {"name": "y", "direction": "output", "width": 1}],
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
    provider = CodexCliSpecProvider(model="gpt-5.6-terra", executable="/usr/bin/codex")
    proposal = provider.propose([{"role": "user", "content": "and gate"}], {})

    assert proposal.ready_for_execution is True
    assert proposal.top == "and2"
    assert "--ephemeral" in captured["command"]
    assert captured["command"][captured["command"].index("--sandbox") + 1] == "read-only"
    assert captured["command"][captured["command"].index("--model") + 1] == "gpt-5.6-terra"


def test_provider_rejects_legacy_generated_rtl():
    with pytest.raises(ValueError, match="cannot include RTL"):
        SpecProposal.from_mapping({
            "objective": "bad", "functionality": "bad", "top": "top",
            "target_platform": "nangate45", "target_stage": "finish",
            "clock_period_ns": 10, "core_utilization_pct": 10,
            "place_density": 0.45,
            "rtl_source": "module top; initial $system(\"sh\"); endmodule",
            "missing_fields": [], "assumptions": [],
            "clarification_questions": [], "ready_for_execution": True,
        })


def test_product_has_no_byok_provider_module_or_export():
    """Internal service auth is the only authority; BYOK code is absent."""
    import openroad_platform_scheduler as scheduler

    assert not hasattr(scheduler, "ProviderProfile")
    assert not hasattr(scheduler, "ProviderProfileStore")
    assert not hasattr(scheduler, "InMemorySecretBroker")
    assert not hasattr(scheduler, "OpenAICompatibleSpecProvider")
