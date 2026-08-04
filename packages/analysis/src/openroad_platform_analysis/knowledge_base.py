"""Evidence-only knowledge index with hard version isolation and safe replay."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openroad_platform_contracts import RepairAction


TOKEN = re.compile(r"[A-Za-z0-9_.-]+|[\u4e00-\u9fff]")


@dataclass(frozen=True)
class EvidenceContext:
    design_id: str
    platform: str
    pdk_id: str
    toolchain_id: str

    def validate(self) -> None:
        for value in dataclasses.astuple(self):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Evidence context fields are required")


@dataclass(frozen=True)
class KnowledgeRecord:
    claim: str
    evidence_ref: str
    evidence_sha256: str
    context: EvidenceContext
    verified: bool
    scope: str = "exact_design"
    tags: tuple[str, ...] = ()
    proposed_action: RepairAction | None = None
    record_id: str = field(default_factory=lambda: f"knowledge-{uuid.uuid4().hex}")

    def validate(self) -> None:
        self.context.validate()
        if not self.verified:
            raise ValueError("Only verified records may enter the knowledge base")
        if not self.claim.strip() or len(self.claim) > 4000:
            raise ValueError("Knowledge claim is empty or too long")
        if not self.evidence_ref.startswith(("artifact:", "run:", "docs/evidence/")):
            raise ValueError("Knowledge record requires a durable evidence reference")
        if not re.fullmatch(r"[0-9a-f]{64}", self.evidence_sha256):
            raise ValueError("Knowledge record requires a SHA-256 evidence digest")
        if self.scope not in {"exact_design", "platform_general"}:
            raise ValueError("Invalid knowledge scope")
        if self.proposed_action:
            self.proposed_action.validate()
            if self.evidence_ref not in self.proposed_action.evidence_refs:
                raise ValueError("Proposed action must cite the record evidence")

    def fingerprint(self) -> str:
        payload = {
            "claim": self.claim, "evidence_ref": self.evidence_ref,
            "evidence_sha256": self.evidence_sha256,
            "context": dataclasses.asdict(self.context), "scope": self.scope,
            "tags": list(self.tags),
            "proposed_action": self.proposed_action.to_dict() if self.proposed_action else None,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True,
                                         ensure_ascii=False).encode()).hexdigest()


class EvidenceKnowledgeBase:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS knowledge_records (
                    record_id TEXT PRIMARY KEY,
                    claim TEXT NOT NULL,
                    evidence_ref TEXT NOT NULL,
                    evidence_sha256 TEXT NOT NULL,
                    design_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    pdk_id TEXT NOT NULL,
                    toolchain_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    proposed_action_json TEXT,
                    fingerprint TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_knowledge_context
                  ON knowledge_records(platform, pdk_id, toolchain_id, design_id);
            """)

    def add(self, record: KnowledgeRecord) -> str:
        record.validate()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO knowledge_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (record.record_id, record.claim, record.evidence_ref,
                 record.evidence_sha256, record.context.design_id,
                 record.context.platform, record.context.pdk_id,
                 record.context.toolchain_id, record.scope,
                 json.dumps(record.tags, ensure_ascii=False),
                 json.dumps(record.proposed_action.to_dict(), ensure_ascii=False)
                 if record.proposed_action else None,
                 record.fingerprint(), datetime.now(timezone.utc).isoformat()),
            )
        return record.record_id

    def search(self, query: str, context: EvidenceContext, *, limit: int = 10) -> list[dict]:
        context.validate()
        terms = set(TOKEN.findall(query.lower()))
        if not terms:
            raise ValueError("Knowledge query has no searchable terms")
        limit = max(1, min(int(limit), 20))
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM knowledge_records
                   WHERE platform = ? AND pdk_id = ? AND toolchain_id = ?
                     AND (design_id = ? OR scope = 'platform_general')""",
                (context.platform, context.pdk_id, context.toolchain_id, context.design_id),
            ).fetchall()
        results = []
        for row in rows:
            haystack = set(TOKEN.findall(
                (row["claim"] + " " + " ".join(json.loads(row["tags_json"]))).lower()
            ))
            overlap = terms & haystack
            if not overlap:
                continue
            score = len(overlap) / max(1, len(terms))
            results.append({
                "record_id": row["record_id"], "claim": row["claim"],
                "score": round(score, 6), "scope": row["scope"],
                "evidence": {"ref": row["evidence_ref"],
                             "sha256": row["evidence_sha256"]},
                "fingerprint": row["fingerprint"],
                "proposed_action": json.loads(row["proposed_action_json"])
                if row["proposed_action_json"] else None,
            })
        results.sort(key=lambda item: (-item["score"], item["record_id"]))
        return results[:limit]

    def replay(self, result: dict[str, Any], context: EvidenceContext) -> dict[str, Any]:
        context.validate()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_records WHERE record_id = ?",
                (result.get("record_id"),),
            ).fetchone()
        if row is None or result.get("fingerprint") != row["fingerprint"]:
            raise ValueError("Knowledge replay fingerprint is missing or stale")
        if (row["platform"], row["pdk_id"], row["toolchain_id"]) != (
            context.platform, context.pdk_id, context.toolchain_id,
        ):
            raise ValueError("Knowledge replay context version mismatch")
        if row["scope"] == "exact_design" and row["design_id"] != context.design_id:
            raise ValueError("Knowledge replay design mismatch")
        if not row["proposed_action_json"]:
            return {"status": "evidence_only", "executed": False,
                    "record_id": row["record_id"], "action": None}
        action = RepairAction.from_dict(json.loads(row["proposed_action_json"]))
        return {"status": "approved_for_policy_evaluation", "executed": False,
                "record_id": row["record_id"], "action": action.to_dict(),
                "evidence": {"ref": row["evidence_ref"],
                             "sha256": row["evidence_sha256"]}}

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection
