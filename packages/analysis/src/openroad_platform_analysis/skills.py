"""Stage 2.2: skills mechanism — reusable, evidence-backed capabilities.

A skill packages a repeatable behavior (e.g. "tighten density after a
routing-congestion observation") with a parameter template, applicability
scoring, and the lessons that back it. Skills never execute EDA themselves;
they produce a reviewable plan (the same planning-layer contract as the
OptimizerAgent).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


@dataclass
class Skill:
    skill_id: str
    name: str
    description: str
    trigger_terms: tuple[str, ...]    # e.g. ("routing", "congestion", "density")
    parameter_template: dict[str, dict[str, Any]]  # name -> {bounds, default}
    lesson_ids: tuple[str, ...] = ()
    created_at: float = field(default_factory=time.time)

    def validate(self) -> None:
        if not self.name.strip() or len(self.name) > 200:
            raise ValueError("Skill name is empty or too long")
        if not self.trigger_terms:
            raise ValueError("Skill requires trigger terms")
        if not self.parameter_template:
            raise ValueError("Skill requires a parameter template")
        for name, spec in self.parameter_template.items():
            if "bounds" not in spec or "default" not in spec:
                raise ValueError(f"Skill parameter {name!r} needs bounds+default")
            low, high = spec["bounds"]
            if not low < high:
                raise ValueError(f"Skill parameter {name!r} bounds are invalid")
            if not low <= spec["default"] <= high:
                raise ValueError(f"Skill parameter {name!r} default out of bounds")

    def fingerprint(self) -> str:
        payload = {"name": self.name, "trigger_terms": list(self.trigger_terms),
                   "parameter_template": self.parameter_template}
        return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                         ensure_ascii=False).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {"skill_id": self.skill_id, "name": self.name,
                "description": self.description,
                "trigger_terms": list(self.trigger_terms),
                "parameter_template": self.parameter_template,
                "lesson_ids": list(self.lesson_ids),
                "created_at": self.created_at}


class SkillsStore:
    """SQLite-backed durable skills (var/public/skills.db)."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS skills_v1 (
                    skill_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    trigger_terms_json TEXT NOT NULL,
                    parameter_template_json TEXT NOT NULL,
                    lesson_ids_json TEXT NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE,
                    created_at REAL NOT NULL
                )""")

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def add(self, skill: Skill) -> str:
        skill.validate()
        with self._connect() as con:
            con.execute(
                """INSERT OR IGNORE INTO skills_v1
                   (skill_id, name, description, trigger_terms_json,
                    parameter_template_json, lesson_ids_json, fingerprint,
                    created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (skill.skill_id, skill.name, skill.description,
                 json.dumps(list(skill.trigger_terms), ensure_ascii=False),
                 json.dumps(skill.parameter_template, ensure_ascii=False),
                 json.dumps(list(skill.lesson_ids), ensure_ascii=False),
                 skill.fingerprint(), skill.created_at),
            )
        return skill.skill_id

    def match(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        """Score skills by how many trigger terms appear in the query."""
        terms = set(w.lower() for w in query.replace("_", " ").split())
        results = []
        for row in self._connect().execute(
            "SELECT * FROM skills_v1 ORDER BY created_at ASC"
        ).fetchall():
            triggers = set(json.loads(row["trigger_terms_json"]))
            overlap = terms & triggers
            if not overlap:
                continue
            results.append({**dict(row),
                            "score": round(len(overlap) / max(1, len(triggers)), 3)})
        results.sort(key=lambda item: (-item["score"], item["name"]))
        return results[:limit]

    def all(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._connect().execute(
            "SELECT * FROM skills_v1 ORDER BY created_at DESC").fetchall()]


def apply_skill(skill: Skill, query: str,
                current: Mapping[str, float] | None = None) -> dict[str, Any]:
    """2.2 planning-side skill application: returns parameter adjustments as
    a reviewable plan (never executes)."""
    current = dict(current or {})
    adjustments: dict[str, float] = {}
    for name, spec in skill.parameter_template.items():
        low, high = spec["bounds"]
        default = float(spec["default"])
        value = current.get(name, default)
        # Move toward the skill's default when it differs from current.
        if abs(value - default) > 1e-9:
            value = default
        adjustments[name] = round(min(high, max(low, value)), 4)
    return {
        "skill_id": skill.skill_id,
        "skill_name": skill.name,
        "matched_query": query,
        "adjustments": adjustments,
        "lesson_ids": list(skill.lesson_ids),
        "execution_allowed": False,
        "required_gate": "human_review",
        "created_at": time.time(),
    }
