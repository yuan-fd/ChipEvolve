"""G11: forbid the defensive patterns that keep dead structure alive."""

from _harness import assert_catches, assert_clean

RULE = "G11"


def test_g11_no_defensive_antipatterns_real_tree_is_clean() -> None:
    assert_clean(RULE)


def test_g11_no_defensive_antipatterns_gate_can_actually_fail() -> None:
    assert_catches(RULE)
