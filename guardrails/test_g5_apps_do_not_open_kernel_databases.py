"""G5: apps must not open kernel-owned SQLite files."""

from _harness import assert_catches, assert_clean

RULE = "G5"


def test_g5_apps_do_not_open_kernel_databases_real_tree_is_clean() -> None:
    assert_clean(RULE)


def test_g5_apps_do_not_open_kernel_databases_gate_can_actually_fail() -> None:
    assert_catches(RULE)
