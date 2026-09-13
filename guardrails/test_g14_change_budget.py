"""G14: one change may touch one architectural layer."""

from pathlib import Path

import rules
from _harness import REPO_ROOT

RULE = "G14"


def test_g14_change_budget_real_tree_is_clean() -> None:
    # A single-layer change is allowed.
    assert rules.change_budget_violations(REPO_ROOT, ["core/runtime/store.py"]) == []


def test_g14_change_budget_gate_can_actually_fail() -> None:
    across = ["core/runtime/store.py", "apps/alpha/main.py", "plugins/orfs/adapter.py"]
    violations = rules.change_budget_violations(REPO_ROOT, across)
    assert violations, "G14 failed to detect a change spanning three layers"
