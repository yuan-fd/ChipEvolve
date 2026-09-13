"""G6:每 app needs its own pyproject and a process entry point."""

from _harness import assert_catches, assert_clean

RULE = "G6"


def test_g6_apps_are_independently_installable_real_tree_is_clean() -> None:
    assert_clean(RULE)


def test_g6_apps_are_independently_installable_gate_can_actually_fail() -> None:
    assert_catches(RULE)
