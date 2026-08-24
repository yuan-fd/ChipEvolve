"""Append-only, falsifiable learning records for v2 self-evolution.

LLM reflections are hypotheses, never observations.  Promotion requires an
intervention result and a held-out-design validation record.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


STATUSES = {"draft", "tested", "supported", "refuted", "validated", "retired"}


def reflection_hypothesis(*, claim: str, mechanism: str, context: Mapping[str, Any],
                          evidence_refs: list[Mapping[str, str]], producer: str,
                          proposed_intervention: Mapping[str, Any]) -> dict[str, Any]:
    """Create a non-executable hypothesis with an explicit possible refutation."""
    if not claim.strip() or not mechanism.strip() or not producer.strip():
        raise ValueError("claim, mechanism and producer are required")
    if not evidence_refs:
        raise ValueError("a reflection needs evidence references")
    if not isinstance(proposed_intervention, Mapping) or not proposed_intervention:
        raise ValueError("a falsifiable intervention is required")
    for item in evidence_refs:
        if not isinstance(item.get("ref"), str) or not isinstance(item.get("sha256"), str) or len(item["sha256"]) != 64:
            raise ValueError("invalid evidence reference")
    identity = hashlib.sha256(json.dumps({"claim": claim, "mechanism": mechanism, "context": dict(context),
                                          "intervention": dict(proposed_intervention)}, sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()[:24]
    return {"schema_version": 1, "hypothesis_id": f"hypothesis-{identity}", "status": "draft",
            "claim": claim.strip(), "mechanism": mechanism.strip(), "context": dict(context),
            "evidence_refs": list(evidence_refs), "producer": producer.strip(),
            "proposed_intervention": dict(proposed_intervention),
            "falsifier": "pre-registered controlled result contradicts the predicted direction",
            "execution_allowed": False}


def assess_hypothesis(hypothesis: Mapping[str, Any], *, intervention_report: Mapping[str, Any],
                      expected_direction: str) -> dict[str, Any]:
    """Assess one local experiment; it cannot promote a reusable rule."""
    if expected_direction not in {"min", "max", "nonzero", "zero"}:
        raise ValueError("direction must be min, max, nonzero, or zero")
    if hypothesis.get("status") not in {"draft", "tested", "supported"}:
        raise ValueError("hypothesis is not assessable")
    eligible = intervention_report.get("causal_eligible") is True
    effect = intervention_report.get("interaction_effect", intervention_report.get("effect"))
    if not eligible or isinstance(effect, bool) or not isinstance(effect, (int, float)):
        outcome, reason = "tested", "insufficient controlled intervention evidence"
    elif ((expected_direction == "min" and effect < 0)
          or (expected_direction == "max" and effect > 0)
          or (expected_direction == "nonzero" and abs(float(effect)) > 1e-12)
          or (expected_direction == "zero" and abs(float(effect)) <= 1e-12)):
        outcome, reason = "supported", "controlled local result matches predicted direction"
    else:
        outcome, reason = "refuted", "controlled local result contradicts predicted direction"
    return {"hypothesis_id": hypothesis["hypothesis_id"], "status": outcome, "reason": reason,
            "intervention_report": dict(intervention_report), "scope": "exact recorded design/context",
            "execution_allowed": False}


def promote_after_holdout(assessment: Mapping[str, Any], holdout: Mapping[str, Any]) -> dict[str, Any]:
    """Make transfer evidence explicit; one successful source run is never enough."""
    if assessment.get("status") != "supported":
        return {"promoted": False, "reason": "source hypothesis is not locally supported", "execution_allowed": False}
    if holdout.get("eligible") is not True or holdout.get("outcome") != "validated":
        return {"promoted": False, "reason": "held-out design did not validate the effect", "execution_allowed": False}
    return {"promoted": True, "status": "validated", "scope": "two named designs under pinned context",
            "required_next_gate": "pre-registered third-design confirmation",
            "execution_allowed": False}


class HypothesisLedger:
    """Small append-only SQLite ledger; records are immutable audit events."""
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve(); self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as c:
            c.execute("CREATE TABLE IF NOT EXISTS hypothesis_events_v1 (event_id TEXT PRIMARY KEY, hypothesis_id TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL)")

    def append(self, record: Mapping[str, Any]) -> str:
        if not isinstance(record.get("hypothesis_id"), str) or record.get("status") not in STATUSES:
            raise ValueError("invalid hypothesis record")
        event_id = f"hyp-event-{uuid.uuid4().hex}"
        with self._connect() as c:
            c.execute("INSERT INTO hypothesis_events_v1 VALUES (?, ?, ?, ?)", (event_id, record["hypothesis_id"], json.dumps(dict(record), sort_keys=True), datetime.now(timezone.utc).isoformat()))
        return event_id

    def history(self, hypothesis_id: str) -> list[dict[str, Any]]:
        with self._connect() as c:
            rows = c.execute("SELECT event_id,payload_json,created_at FROM hypothesis_events_v1 WHERE hypothesis_id=? ORDER BY created_at", (hypothesis_id,)).fetchall()
        return [{"event_id": row[0], "record": json.loads(row[1]), "created_at": row[2]} for row in rows]

    def _connect(self): return sqlite3.connect(self.path)
