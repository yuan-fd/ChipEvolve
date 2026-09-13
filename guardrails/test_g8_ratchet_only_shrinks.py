"""G8: recorded sizes may only go down; growth needs an approval."""

from _harness import assert_catches, assert_clean

RULE = "G8"


def test_g8_ratchet_only_shrinks_real_tree_is_clean() -> None:
    assert_clean(RULE)


def test_g8_ratchet_only_shrinks_gate_can_actually_fail() -> None:
    assert_catches(RULE)
