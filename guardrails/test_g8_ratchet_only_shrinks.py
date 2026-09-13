"""G8: recorded sizes may only go down; growth needs an approval.

The negative fixture proves the gate can fail at all.  The cases below prove it
can fail *again later*, which is the harder half: an approval that only had to
exist kept exempting whatever it named, so the kernel grew past its budget with
every gate green.  The amount is now part of the approval.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from _harness import assert_catches, assert_clean

import rules

RULE = "G8"


def test_g8_ratchet_only_shrinks_real_tree_is_clean() -> None:
    assert_clean(RULE)


def test_g8_ratchet_only_shrinks_gate_can_actually_fail() -> None:
    assert_catches(RULE)


def grown_tree(tmp_path: Path) -> Path:
    """A kernel three lines long against a budget of one."""
    (tmp_path / "guardrails").mkdir(parents=True, exist_ok=True)
    (tmp_path / "guardrails" / "baseline.json").write_text(
        json.dumps({"core_total_loc": 1}), encoding="utf-8")
    (tmp_path / "core").mkdir(exist_ok=True)
    (tmp_path / "core" / "grown.py").write_text("A = 1\nB = 2\nC = 3\n",
                                                encoding="utf-8")
    (tmp_path / "approvals").mkdir(exist_ok=True)
    return tmp_path


def grant(root: Path, key: str, ceiling: int) -> None:
    """Write an approval: a written reason, and the amount it authorises."""
    safe = key.replace("/", "_")
    (root / "approvals" / f"{safe}.md").write_text(
        f"# Approval: {key} -> {ceiling}\n\nBecause.\n", encoding="utf-8")
    (root / "approvals" / "ceiling.json").write_text(
        json.dumps({key: ceiling}), encoding="utf-8")


def test_an_approval_must_cover_the_size_it_authorises(tmp_path: Path) -> None:
    """An approval for less than the tree actually is, is not an approval.

    This is the hole that let the kernel sit above its budget with every gate
    green: the check was "does a file exist", so the first approval exempted the
    budget for the rest of the project's life.
    """
    root = grown_tree(tmp_path)
    grant(root, "core_total_loc", 2)
    assert [v for v in rules.scan(root, RULE) if v.rule == RULE], (
        "an approval for 2 lines must not exempt a 3-line kernel"
    )


def test_an_approval_that_covers_the_size_is_accepted(tmp_path: Path) -> None:
    """The other direction, so the gate is not simply always angry."""
    root = grown_tree(tmp_path)
    grant(root, "core_total_loc", 3)
    assert rules.scan(root, RULE) == []


def test_a_reason_without_a_number_is_not_an_approval(tmp_path: Path) -> None:
    """Prose alone cannot authorise a size: nothing would bound it."""
    root = grown_tree(tmp_path)
    (root / "approvals" / "core_total_loc.md").write_text(
        "# Approval\n\nBecause I said so.\n", encoding="utf-8")
    assert [v for v in rules.scan(root, RULE) if v.rule == RULE]


def test_an_unreadable_grant_is_not_an_approval(tmp_path: Path) -> None:
    """A corrupt number must fail closed, not pass by accident."""
    root = grown_tree(tmp_path)
    grant(root, "core_total_loc", 3)
    (root / "approvals" / "ceiling.json").write_text("{not json",
                                                     encoding="utf-8")
    assert [v for v in rules.scan(root, RULE) if v.rule == RULE]


def test_the_real_approvals_name_the_size_they_authorise() -> None:
    """Every approval in this repository is a number, not just a document.

    Checked against the real tree so the two cannot drift apart: a reason file
    with no grant would leave the component unapproved, and a grant with no
    reason would make the ratchet a spreadsheet.
    """
    root = Path(__file__).resolve().parents[1]
    grants = json.loads((root / "approvals" / "ceiling.json").read_text(
        encoding="utf-8"))
    # A key that names a path is stored as it appears in the baseline and looked
    # up under its sanitised name, so the two are compared the way the gate does.
    real = {key: value for key, value in grants.items()
            if not key.startswith("_") and isinstance(value, int)}
    sanitised = {re.sub(r"[^A-Za-z0-9_.-]", "_", key) for key in real}
    for path in sorted((root / "approvals").glob("*.md")):
        key = path.stem
        if key == "core_total_loc_2":  # a superseded ceiling, kept as history
            continue
        assert key in sanitised, f"{path.name} authorises no size"
    assert real, "no approval names a size at all"
