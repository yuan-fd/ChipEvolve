"""Shared harness for the guardrail gates.

Two assertions matter for every rule:

``assert_clean``   - the real tree must produce zero violations
``assert_catches`` - the rule's own negative fixture must produce violations

The second one is what separates a gate from a comment.  A rule whose fixture
passes has never been shown to detect anything.
"""

from __future__ import annotations

from pathlib import Path

import rules

REPO_ROOT = Path(__file__).resolve().parents[1]
NEGATIVE_ROOT = Path(__file__).resolve().parent / "negative"


def assert_clean(rule: str) -> None:
    """The real repository must satisfy *rule*."""
    violations = rules.scan(REPO_ROOT, rule)
    assert violations == [], (
        f"{rule} violated in the real tree:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def assert_catches(rule: str) -> None:
    """The rule's negative fixture must be detected.

    Without this, a gate could be written so loosely that it always passes and
    nobody would notice.  v1 shipped exactly that failure mode: a "boundary"
    test that only asserted a filename pattern.
    """
    fixture = NEGATIVE_ROOT / rule
    assert fixture.is_dir(), (
        f"{rule} has no negative fixture at {fixture}; an unproven gate is not a gate"
    )
    violations = rules.scan(fixture, rule)
    assert violations, (
        f"{rule} failed to detect its own negative fixture at {fixture}. "
        f"The gate does not actually work."
    )
