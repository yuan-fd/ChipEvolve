import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/analyze_v2_paper_edair_qa.py"
SPEC = importlib.util.spec_from_file_location("edair_stats", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MODULE)


def test_exact_sign_flip_and_bootstrap_are_deterministic():
    values = [1.0, 1.0, 1.0, 1.0, 1.0]
    test = MODULE._sign_flip(values)
    interval = MODULE._bootstrap_mean(values, seed=7, draws=100)

    assert test["draws"] == 32
    assert test["p_value"] == 2 / 32
    assert interval["estimate"] == 1.0
    assert interval["lower"] == 1.0
    assert interval["upper"] == 1.0


def test_holm_adjustment_is_monotonic():
    adjusted = MODULE._holm({"a": .01, "b": .03, "c": .2})
    assert adjusted["a"]["holm_adjusted_p_value"] == .03
    assert adjusted["b"]["holm_adjusted_p_value"] == .06
    assert adjusted["c"]["holm_adjusted_p_value"] == .2


def test_four_design_sign_flip_cannot_claim_small_p_from_repeated_calls():
    result = MODULE._sign_flip([.9, .8, .7, .6])
    assert result["draws"] == 16
    assert result["p_value"] == 2 / 16
