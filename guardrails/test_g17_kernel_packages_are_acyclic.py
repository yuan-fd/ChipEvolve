"""G17: no two kernel packages may import each other.

The cycle this rule exists for was real, and every other gate here would have
missed it: it was written as an import inside a function, so it appeared in no
module header and broke no test.
"""

from _harness import assert_catches, assert_clean

RULE = "G17"


def test_g17_kernel_packages_are_acyclic_real_tree_is_clean() -> None:
    assert_clean(RULE)


def test_g17_kernel_packages_are_acyclic_gate_can_actually_fail() -> None:
    assert_catches(RULE)
