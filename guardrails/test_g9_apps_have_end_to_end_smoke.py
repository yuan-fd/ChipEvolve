"""G9: each app ships a real end-to-end smoke."""

from _harness import assert_catches, assert_clean

RULE = "G9"


def test_g9_apps_have_end_to_end_smoke_real_tree_is_clean() -> None:
    assert_clean(RULE)


def test_g9_apps_have_end_to_end_smoke_gate_can_actually_fail() -> None:
    assert_catches(RULE)
