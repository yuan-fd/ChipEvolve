import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/analyze_v2_paper_rtl_matrix.py"
SPEC = importlib.util.spec_from_file_location("rtl_stats", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MODULE)


def test_pass_at_k_is_exact_and_monotonic():
    values = [MODULE._pass_at_k(5, 2, k) for k in range(1, 6)]
    assert values[0] == .4
    assert values[-1] == 1.0
    assert values == sorted(values)
    assert MODULE._pass_at_k(5, 0, 5) == 0.0
