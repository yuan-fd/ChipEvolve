"""G7: no source file may exceed the per-file line ceiling."""

from _harness import assert_catches, assert_clean

RULE = "G7"


def test_g7_file_size_ceiling_real_tree_is_clean() -> None:
    assert_clean(RULE)


def test_g7_file_size_ceiling_gate_can_actually_fail() -> None:
    assert_catches(RULE)
