"""G15: every rule in AGENTS.md has a gate and a negative fixture."""

from _harness import assert_catches, assert_clean

RULE = "G15"


def test_g15_declared_rules_have_gates_real_tree_is_clean() -> None:
    assert_clean(RULE)


def test_g15_declared_rules_have_gates_gate_can_actually_fail() -> None:
    assert_catches(RULE)
