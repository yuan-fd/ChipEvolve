import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_hidden_rtl_reference_matches_generated_backend_and_is_not_agent_input():
    protocol = json.loads((ROOT / "experiments/v2-paper-20260825/rtl-reference-protocol.json").read_text())
    assert protocol["platform"] == "sky130hd"
    assert protocol["target_stage"] == "finish"
    assert protocol["replicas"] == 3
    assert "unavailable to generation agents" in protocol["purpose"]
