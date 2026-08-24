"""Independent RTL verification evidence and deterministic mutation scoring.

This module deliberately does *not* generate RTL.  A testbench author (human
or a separately configured Verification Agent) may provide a frozen oracle;
the results below make its demonstrated fault-detection strength explicit.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping


@dataclass(frozen=True)
class Mutation:
    mutation_id: str
    operator: str
    source_sha256: str
    mutated_source: str
    location: int

    def to_dict(self) -> dict[str, Any]:
        return {"mutation_id": self.mutation_id, "operator": self.operator,
                "source_sha256": self.source_sha256, "mutated_source_sha256": _sha(self.mutated_source),
                "location": self.location}


# Conservative syntactic operators.  Each result is still a real compilation
# and simulation; an un-compilable mutant is reported separately, never passed
# off as a killed functional bug.
_OPERATORS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("eq_to_ne", re.compile(r"==(?!=)"), "!="),
    ("ne_to_eq", re.compile(r"!="), "=="),
    ("plus_to_minus", re.compile(r"(?<!\+)\+(?!\+)"), "-"),
    ("minus_to_plus", re.compile(r"(?<!-)\-(?!-)"), "+"),
    ("and_to_or", re.compile(r"&&"), "||"),
    ("or_to_and", re.compile(r"\|\|"), "&&"),
    ("zero_to_one", re.compile(r"(?<![\w'])0(?![\w])"), "1"),
    ("one_to_zero", re.compile(r"(?<![\w'])1(?![\w])"), "0"),
)


def generate_mutants(source: str, *, maximum: int = 64) -> tuple[Mutation, ...]:
    """Create bounded single-site mutants from synthesizable source text."""
    if not source.strip() or not 1 <= maximum <= 512:
        raise ValueError("source is empty or mutation bound is invalid")
    source_sha = _sha(source)
    result: list[Mutation] = []
    for operator, pattern, replacement in _OPERATORS:
        for match in pattern.finditer(source):
            mutated = source[:match.start()] + replacement + source[match.end():]
            if mutated == source:
                continue
            seed = f"{source_sha}:{operator}:{match.start()}:{_sha(mutated)}"
            result.append(Mutation(f"mut-{hashlib.sha256(seed.encode()).hexdigest()[:20]}",
                                   operator, source_sha, mutated, match.start()))
            if len(result) >= maximum:
                return tuple(result)
    return tuple(result)


def mutation_report(mutants: Iterable[Mutation], outcomes: Mapping[str, str], *,
                    testbench_sha256: str, verifier_identity: str,
                    minimum_score: float = 0.80) -> dict[str, Any]:
    """Summarise real mutant outcomes without confusing compile errors with kills.

    ``outcomes`` values are ``killed`` (the oracle rejected a compiling mutant),
    ``survived``, ``invalid`` (the mutant did not compile), or ``not_run``.
    """
    if not re.fullmatch(r"[0-9a-f]{64}", testbench_sha256):
        raise ValueError("testbench_sha256 must be a SHA-256 digest")
    if not verifier_identity.strip() or not 0 < minimum_score <= 1:
        raise ValueError("verifier identity or required mutation score is invalid")
    rows = list(mutants)
    if not rows:
        return {"eligible": False, "reason": "no generated mutants", "execution_allowed": False}
    allowed = {"killed", "survived", "invalid", "not_run"}
    normalized = []
    for item in rows:
        status = outcomes.get(item.mutation_id, "not_run")
        if status not in allowed:
            raise ValueError(f"invalid mutation outcome: {status}")
        normalized.append({**item.to_dict(), "outcome": status})
    executable = [item for item in normalized if item["outcome"] in {"killed", "survived"}]
    killed = sum(item["outcome"] == "killed" for item in executable)
    score = (killed / len(executable)) if executable else 0.0
    return {
        "schema_version": 1, "kind": "mutation_evidence", "verifier_identity": verifier_identity,
        "testbench_sha256": testbench_sha256, "source_sha256": rows[0].source_sha256,
        "mutants": normalized, "generated_count": len(rows), "executable_count": len(executable),
        "killed_count": killed, "survived_count": len(executable) - killed,
        "invalid_count": sum(item["outcome"] == "invalid" for item in normalized),
        "not_run_count": sum(item["outcome"] == "not_run" for item in normalized),
        "mutation_score": score, "minimum_score": minimum_score,
        "eligible": bool(executable) and score >= minimum_score,
        "claim": "testbench fault-detection evidence only; not a proof of functional correctness",
        "execution_allowed": False,
    }


def independent_verification_gate(*, candidate_generator: str, verifier_identity: str,
                                  testbench_origin: str, report: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed when an unreviewed self-authored oracle is used as evidence."""
    independent = candidate_generator.strip() != verifier_identity.strip()
    valid_origin = testbench_origin in {"user_authored", "project_existing", "reference_model", "approved_generated"}
    return {"independent_author": independent, "accepted_origin": valid_origin,
            "mutation_eligible": report.get("eligible") is True,
            "accepted": independent and valid_origin and report.get("eligible") is True,
            "execution_allowed": False}


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
