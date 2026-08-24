import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_agent_ablation_separates_reliability_from_qor_claims():
    protocol = json.loads((ROOT / "experiments/v2-paper-20260825/agent-protocol.json").read_text())
    assert len(protocol["arms"]) == 4
    assert "does not claim" in protocol["claim_boundary"]
    source = (ROOT / "scripts/run_v2_paper_agent_ablation.py").read_text()
    assert "test_candidate_round_recovers_partial_replica_submission" in source
    assert "below_threshold_positive_candidates" in source
