"""G16: the kernel must not import an app or a plugin.

Measured at zero in this tree.  The gate exists because zero is a fact about
today, not a property of the design: one convenient import would end it, and no
other gate would notice.
"""

from _harness import assert_catches, assert_clean

RULE = "G16"


def test_g16_kernel_does_not_import_capabilities_real_tree_is_clean() -> None:
    assert_clean(RULE)


def test_g16_kernel_does_not_import_capabilities_gate_can_actually_fail() -> None:
    assert_catches(RULE)
