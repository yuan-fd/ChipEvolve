"""G4: apps reach the platform only via contracts + core client."""

from _harness import assert_catches, assert_clean
from rules import app_forbidden_core_import_violations

RULE = "G4"


def test_g4_apps_only_import_contracts_and_client_real_tree_is_clean() -> None:
    assert_clean(RULE)


def test_g4_apps_only_import_contracts_and_client_gate_can_actually_fail() -> None:
    assert_catches(RULE)


def test_g4_rejects_real_platform_namespace_imports(tmp_path) -> None:
    app = tmp_path / "apps" / "example"
    app.mkdir(parents=True)
    (app / "main.py").write_text(
        "from openroad_platform_runtime import RuntimeStore\n",
        encoding="utf-8",
    )

    violations = app_forbidden_core_import_violations(tmp_path)

    assert violations
    assert all(violation.rule == RULE for violation in violations)
    assert all("openroad_platform_runtime" in violation.detail
                for violation in violations)
