"""G12: statements after a terminal statement can never run."""

from _harness import assert_catches, assert_clean

RULE = "G12"


def test_g12_no_unreachable_code_real_tree_is_clean() -> None:
    assert_clean(RULE)


def test_g12_no_unreachable_code_gate_can_actually_fail() -> None:
    assert_catches(RULE)
