"""G4: apps reach the platform only via contracts + core client."""

from _harness import assert_catches, assert_clean

RULE = "G4"


def test_g4_apps_only_import_contracts_and_client_real_tree_is_clean() -> None:
    assert_clean(RULE)


def test_g4_apps_only_import_contracts_and_client_gate_can_actually_fail() -> None:
    assert_catches(RULE)
