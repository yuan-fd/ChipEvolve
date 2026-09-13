"""G10: protected components may not change silently."""

from _harness import assert_catches, assert_clean

RULE = "G10"


def test_g10_protected_files_are_hash_locked_real_tree_is_clean() -> None:
    assert_clean(RULE)


def test_g10_protected_files_are_hash_locked_gate_can_actually_fail() -> None:
    assert_catches(RULE)
