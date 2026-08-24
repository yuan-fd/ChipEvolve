"""Turn bounded intervention evidence into a falsifiable next-study brief.

This is deliberately not a reward model and cannot submit a task.  Its role is
to prevent the agent from turning a one-design interaction into a universal
rule: a measured local effect becomes a named hypothesis plus a pre-registered
held-out validation experiment.
"""
from __future__ import annotations

from typing import Any, Mapping


def followup_from_interaction(report: Mapping[str, Any], *, first: str,
                              second: str, metric: str) -> dict[str, Any]:
    """Return an evidence-bound cross-design validation plan, or fail closed."""
    if not report.get("causal_eligible"):
        return {"learning_eligible": False,
                "reason": "no balanced repeated local intervention evidence",
                "execution_allowed": False}
    interaction = report.get("interaction_effect")
    levels = report.get("levels")
    if (isinstance(interaction, bool) or not isinstance(interaction, (int, float))
            or not isinstance(levels, Mapping)
            or not isinstance(levels.get(first), list)
            or not isinstance(levels.get(second), list)):
        return {"learning_eligible": False, "reason": "malformed causal report",
                "execution_allowed": False}
    magnitude = abs(float(interaction))
    kind = "interaction" if magnitude > 1e-12 else "no_observed_interaction"
    if kind == "interaction":
        hypothesis = (
            f"Within the recorded context, the effect of {second} on {metric} "
            f"depends on {first}; estimated difference-in-differences is {interaction:g}."
        )
        question = (f"Does the {first} × {second} interaction for {metric} reproduce "
                    "on a held-out design under the same PDK/toolchain?")
    else:
        hypothesis = (f"Within the recorded context, no nonzero {first} × {second} "
                      f"interaction was measured for {metric}.")
        question = (f"Is the absent {first} × {second} interaction for {metric} stable "
                    "on a held-out design?")
    return {
        "learning_eligible": True,
        "evidence_level": "controlled_local_intervention",
        "hypothesis_kind": kind,
        "hypothesis": hypothesis,
        "scope": "exact recorded design/context only; not a transferable rule",
        "next_study": {
            "purpose": "cross_design_holdout_validation",
            "question": question,
            "required_controls": [
                "new design fingerprint not present in source experiment",
                "same PDK, toolchain commit, metric parser and target stage",
                "same two levels for each intervention parameter",
                "at least two independent repetitions at every 2x2 corner",
                "pre-register QoR hard constraints before execution",
            ],
            "parameter_grid": {first: list(levels[first]), second: list(levels[second])},
            "minimum_runs": 8,
        },
        "execution_allowed": False,
    }


def teacher_context_from_holdout(source: Mapping[str, Any], holdout: Mapping[str, Any],
                                 validation: Mapping[str, Any], *, first: str,
                                 second: str, metric: str) -> dict[str, Any]:
    """Make a bounded, auditable mechanism brief for a planning agent.

    A two-design replication is useful evidence, but it is not a universal
    parameter recipe.  This function intentionally returns an *advisory*
    context item: a Teacher may use it to choose a confirmation experiment or
    explain a compound condition, never to launch a new run by itself.
    """
    if not validation.get("eligible"):
        return {"available": False, "reason": str(validation.get("reason") or "ineligible holdout"),
                "execution_allowed": False}
    outcome = validation.get("outcome")
    source_effect = source.get("interaction_effect")
    holdout_effect = holdout.get("interaction_effect")
    if (outcome not in {"validated", "rejected"}
            or isinstance(source_effect, bool) or not isinstance(source_effect, (int, float))
            or isinstance(holdout_effect, bool) or not isinstance(holdout_effect, (int, float))):
        return {"available": False, "reason": "malformed eligible holdout result",
                "execution_allowed": False}
    if outcome == "validated":
        evidence_class = "replicated_compound_condition"
        planning_guidance = (
            "Treat the two parameters as a coupled condition when planning the next "
            "bounded study; retain both levels and test a third held-out design before "
            "any broader rule is proposed."
        )
    else:
        evidence_class = "negative_transfer_evidence"
        planning_guidance = (
            "Do not reuse the source interaction as a general rule. Diagnose the differing "
            "design features and record a new, narrower hypothesis before another study."
        )
    return {
        "available": True,
        "evidence_class": evidence_class,
        "scope": "two named RTL fingerprints under one pinned non-design context",
        "compound_condition": {"first_parameter": first, "second_parameter": second,
                               "metric": metric, "source_interaction": float(source_effect),
                               "holdout_interaction": float(holdout_effect)},
        "planning_guidance": planning_guidance,
        "required_next_gate": "human-reviewed third-design confirmation",
        "execution_allowed": False,
    }
