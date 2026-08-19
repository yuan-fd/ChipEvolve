"""Stage 2.1: durable lessons mechanism for the self-evolving loop.

Lessons are distilled from optimizer iterations / verified runs, stored
durably, and retrieved to bias later hypotheses. A lesson is only admitted
after its evidence is verified (observed metric beats the prior best), so
failed or unverified attempts never become ground truth — mirroring the
platform's evidence-first rule.
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

LESSON_KINDS = ("parameter_effect", "stall_redirect", "budget_note",
                "design_insight", "failure_pattern")


@dataclass
class Lesson:
    lesson_id: str
    kind: str                     # one of LESSON_KINDS
    claim: str
    context_fingerprint: str
    evidence: tuple[dict, ...]    # verified evidence pointers
    tags: tuple[str, ...] = ()
    confidence: float = 0.5       # 0..1
    created_at: float = field(default_factory=time.time)

    def validate(self) -> None:
        if self.kind not in LESSON_KINDS:
            raise ValueError(f"Invalid lesson kind: {self.kind!r}")
        if not self.claim.strip() or len(self.claim) > 4000:
            raise ValueError("Lesson claim is empty or too long")
        if not self.context_fingerprint.strip():
            raise ValueError("Lesson requires a context fingerprint")
        if not self.evidence:
            raise ValueError("Lesson requires verified evidence")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Lesson confidence must be in [0, 1]")

    def fingerprint(self) -> str:
        payload = {"kind": self.kind, "claim": self.claim,
                   "context_fingerprint": self.context_fingerprint,
                   "tags": list(self.tags)}
        return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                         ensure_ascii=False).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {"lesson_id": self.lesson_id, "kind": self.kind, "claim": self.claim,
                "context_fingerprint": self.context_fingerprint,
                "evidence": list(self.evidence), "tags": list(self.tags),
                "confidence": self.confidence, "created_at": self.created_at}


class LessonsStore:
    """SQLite-backed durable lessons (var/public/lessons.db)."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS lessons_v1 (
                    lesson_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    claim TEXT NOT NULL,
                    context_fingerprint TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE,
                    created_at REAL NOT NULL
                )""")
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_lessons_context
                ON lessons_v1(context_fingerprint, kind)""")

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def add(self, lesson: Lesson) -> str:
        lesson.validate()
        with self._connect() as con:
            con.execute(
                """INSERT OR IGNORE INTO lessons_v1
                   (lesson_id, kind, claim, context_fingerprint, evidence_json,
                    tags_json, confidence, fingerprint, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (lesson.lesson_id, lesson.kind, lesson.claim,
                 lesson.context_fingerprint,
                 json.dumps(list(lesson.evidence), ensure_ascii=False),
                 json.dumps(list(lesson.tags), ensure_ascii=False),
                 lesson.confidence, lesson.fingerprint(), lesson.created_at),
            )
        return lesson.lesson_id

    def search(self, context_fingerprint: str, *, limit: int = 8,
               kinds: Sequence[str] | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 30))
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            rows = self._connect().execute(
                f"""SELECT * FROM lessons_v1
                    WHERE context_fingerprint = ? AND kind IN ({placeholders})
                    ORDER BY confidence DESC, created_at DESC LIMIT ?""",
                (context_fingerprint, *kinds, limit),
            ).fetchall()
        else:
            rows = self._connect().execute(
                """SELECT * FROM lessons_v1
                   WHERE context_fingerprint = ?
                   ORDER BY confidence DESC, created_at DESC LIMIT ?""",
                (context_fingerprint, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._connect().execute(
            "SELECT * FROM lessons_v1 ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 50)),),
        ).fetchall()
        return [dict(row) for row in rows]

    def count(self) -> int:
        return int(self._connect().execute(
            "SELECT COUNT(*) FROM lessons_v1").fetchone()[0])


def distill_lesson(
    *,
    kind: str,
    claim: str,
    context_fingerprint: str,
    evidence: Iterable[dict] | None = None,
    confidence: float = 0.5,
    tags: Iterable[str] = (),
) -> Lesson:
    """Create a verified lesson (caller supplies the verified evidence)."""
    return Lesson(
        lesson_id=f"lesson-{uuid.uuid4().hex[:16]}",
        kind=kind, claim=claim, context_fingerprint=context_fingerprint,
        evidence=tuple(evidence or ()), tags=tuple(tags),
        confidence=max(0.0, min(1.0, float(confidence))),
    )


def lesson_from_iteration(
    *,
    context_fingerprint: str,
    round_no: int,
    parameter: str,
    old_value: float,
    new_value: float,
    metric_name: str,
    direction: str,
    improved: bool,
    metric_value: float,
    previous_metric: float,
) -> Lesson | None:
    """2.1 auto-distillation from an optimizer round: a lesson is only kept
    when the round actually improved the verified metric."""
    if not improved:
        return None
    delta = (new_value - old_value)
    sign = "+" if delta >= 0 else "-"
    claim = (f"round {round_no}: moving {parameter} {old_value:g} -> {new_value:g} "
             f"({sign}%) improved {metric_name} from {previous_metric:g} to "
             f"{metric_value:g} (direction {direction})")
    return Lesson(
        lesson_id=f"lesson-{uuid.uuid4().hex[:16]}",
        kind="parameter_effect", claim=claim,
        context_fingerprint=context_fingerprint,
        evidence=[{"round": round_no, "metric_name": metric_name,
                   "metric_value": metric_value,
                   "previous_metric": previous_metric,
                   "direction": direction}],
        confidence=0.6, tags=(parameter, metric_name),
    )
