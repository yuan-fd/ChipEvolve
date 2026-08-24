import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_rtl_paper_protocol_has_repeated_attempts_and_honest_failure_policy():
    protocol = json.loads((ROOT / "experiments/v2-paper-20260825/rtl-protocol.json").read_text())
    assert protocol["attempts_per_design"] == 5
    assert protocol["designs"] == ["gcd", "fifo", "uart_tx", "ibex_alu"]
    assert "every failed" in protocol["failure_policy"]
    assert "identical output hashes" in protocol["failure_policy"]
    source = (ROOT / "packages/execution/src/openroad_platform_execution/rtlscout_adapter.py").read_text()
    assert "candidate_history.json" in source
    assert "candidate_sha256" in source
