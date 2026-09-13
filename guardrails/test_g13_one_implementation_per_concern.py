"""G13: reuse the existing implementation instead of writing a second."""

from _harness import assert_catches, assert_clean

RULE = "G13"


def test_g13_one_implementation_per_concern_real_tree_is_clean() -> None:
    assert_clean(RULE)


def test_g13_one_implementation_per_concern_gate_can_actually_fail() -> None:
    assert_catches(RULE)
