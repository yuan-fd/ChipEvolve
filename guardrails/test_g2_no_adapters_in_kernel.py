"""G2: no *_plugin.py / *_adapter.py inside the kernel."""

from _harness import assert_catches, assert_clean

RULE = "G2"


def test_g2_no_adapters_in_kernel_real_tree_is_clean() -> None:
    assert_clean(RULE)


def test_g2_no_adapters_in_kernel_gate_can_actually_fail() -> None:
    assert_catches(RULE)
