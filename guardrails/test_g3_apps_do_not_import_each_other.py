"""G3: one app must never import a sibling app."""

from _harness import assert_catches, assert_clean

RULE = "G3"


def test_g3_apps_do_not_import_each_other_real_tree_is_clean() -> None:
    assert_clean(RULE)


def test_g3_apps_do_not_import_each_other_gate_can_actually_fail() -> None:
    assert_catches(RULE)
