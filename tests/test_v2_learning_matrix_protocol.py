import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_learning_protocol_covers_all_ordered_pairs_and_real_randomization():
    learning = json.loads((ROOT / "experiments/v2-paper-20260825/learning-protocol.json").read_text())
    assert len(learning["designs"]) * (len(learning["designs"]) - 1) == 12
    assert learning["replicas_per_corner"] == 3
    assert "OR_SEED" in learning["paired_randomization"]
    runner = (ROOT / "scripts/run_v2_paper_learning_matrix.py").read_text()
    assert "itertools.permutations" in runner
    assert '"--seed"' in runner
    assert "except subprocess.TimeoutExpired" in runner
    assert "final reconciliation skipped" in runner
