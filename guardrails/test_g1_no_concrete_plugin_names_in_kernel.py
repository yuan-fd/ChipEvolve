"""G1: the kernel must not name a plugin, tool, or vendor."""

from _harness import assert_catches, assert_clean

RULE = "G1"


def test_g1_no_concrete_plugin_names_in_kernel_real_tree_is_clean() -> None:
    assert_clean(RULE)


def test_g1_no_concrete_plugin_names_in_kernel_gate_can_actually_fail() -> None:
    assert_catches(RULE)
