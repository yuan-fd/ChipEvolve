"""Evidence RAG v2 with hard context isolation and deterministic reranking."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import re
import sqlite3
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openroad_platform_contracts import EvidencePointer, LearningContext


WORD = re.compile(r"[A-Za-z0-9_.:-]+|[\u4e00-\u9fff]")
# Evidence taxonomy deliberately mirrors the useful part of DPLEvolve's
# Teacher knowledge contract.  In particular, a paper or external repository
# is a *reference donor*, never a local observation or an executable rule.
AUTO_KNOWLEDGE_TYPES = {"observed_fact", "validated_rule"}
KNOWLEDGE_TYPES = AUTO_KNOWLEDGE_TYPES | {
    "hypothesis", "failed_attempt", "negative_evidence",
    "reference_donor", "contract", "deprecated_context",
}


def _tokens(text: str) -> list[str]:
    base = [item.lower() for item in WORD.findall(text)]
    chinese = [item for item in base if len(item) == 1 and "\u4e00" <= item <= "\u9fff"]
    bigrams = [chinese[index] + chinese[index + 1]
               for index in range(max(0, len(chinese) - 1))]
    return base + bigrams


def _fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class EvidenceKnowledgeRecordV2:
    claim: str
    knowledge_type: str
    context: LearningContext
    evidence: EvidencePointer
    verified: bool
    scope: str = "exact_design"
    tags: tuple[str, ...] = ()
    record_id: str = field(default_factory=lambda: f"knowledge-v2-{uuid.uuid4().hex}")

    def validate(self) -> None:
        if not self.claim.strip() or len(self.claim) > 8000:
            raise ValueError("Knowledge claim is empty or too long")
        if self.knowledge_type not in KNOWLEDGE_TYPES:
            raise ValueError("Invalid knowledge_type")
        if self.knowledge_type in AUTO_KNOWLEDGE_TYPES and not self.verified:
            raise ValueError("Action-eligible knowledge must be verified")
        if self.scope not in {"exact_design", "platform_general"}:
            raise ValueError("Invalid knowledge scope")
        self.context.validate()
        self.evidence.validate()
        if not all(isinstance(item, str) and item.strip() and len(item) <= 128
                   for item in self.tags):
            raise ValueError("Invalid knowledge tags")

    @property
    def fingerprint(self) -> str:
        self.validate()
        return _fingerprint({
            "claim": self.claim, "knowledge_type": self.knowledge_type,
            "context": self.context.to_dict(), "evidence": self.evidence.to_dict(),
            "verified": self.verified, "scope": self.scope, "tags": list(self.tags),
        })


@dataclass(frozen=True)
class EvidenceBundle:
    query: str
    context_fingerprint: str
    records: tuple[dict[str, Any], ...]
    bundle_fingerprint: str
    execution_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class EvidenceRAG:
    """SQLite-backed retriever; contextual filters run before text scoring."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS evidence_knowledge_v2 (
                    record_id TEXT PRIMARY KEY,
                    claim TEXT NOT NULL,
                    knowledge_type TEXT NOT NULL,
                    verified INTEGER NOT NULL,
                    scope TEXT NOT NULL,
                    design_id TEXT NOT NULL,
                    design_fingerprint TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    pdk_id TEXT NOT NULL,
                    toolchain_id TEXT NOT NULL,
                    flow_stage TEXT NOT NULL,
                    metric_parser_version TEXT NOT NULL,
                    context_fingerprint TEXT NOT NULL,
                    evidence_ref TEXT NOT NULL,
                    evidence_sha256 TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    terms_json TEXT NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_evidence_rag_context_v2
                ON evidence_knowledge_v2(platform, pdk_id, toolchain_id, flow_stage,
                                         design_id, design_fingerprint);
            """)

    def add(self, record: EvidenceKnowledgeRecordV2) -> str:
        record.validate()
        terms = _tokens(record.claim + " " + " ".join(record.tags))
        if not terms:
            raise ValueError("Knowledge record has no searchable terms")
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO evidence_knowledge_v2 VALUES
                   (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (record.record_id, record.claim, record.knowledge_type,
                 int(record.verified), record.scope, record.context.design_id,
                 record.context.design_fingerprint, record.context.platform,
                 record.context.pdk_id, record.context.toolchain_id,
                 record.context.flow_stage, record.context.metric_parser_version,
                 record.context.fingerprint, record.evidence.ref,
                 record.evidence.sha256, json.dumps(record.tags, ensure_ascii=False),
                 json.dumps(terms, ensure_ascii=False), record.fingerprint,
                 datetime.now(timezone.utc).isoformat()),
            )
        return record.record_id

    def retrieve(self, query: str, context: LearningContext, *, limit: int = 8,
                 action_eligible_only: bool = False) -> EvidenceBundle:
        context.validate()
        terms = _tokens(query)
        if not terms:
            raise ValueError("Evidence query has no searchable terms")
        limit = max(1, min(int(limit), 20))
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM evidence_knowledge_v2
                   WHERE platform = ? AND pdk_id = ? AND toolchain_id = ?
                     AND metric_parser_version = ? AND flow_stage = ?
                     AND ((scope = 'exact_design' AND design_id = ?
                           AND design_fingerprint = ?)
                          OR scope = 'platform_general')""",
                (context.platform, context.pdk_id, context.toolchain_id,
                 context.metric_parser_version, context.flow_stage,
                 context.design_id, context.design_fingerprint),
            ).fetchall()
        if action_eligible_only:
            rows = [row for row in rows if row["knowledge_type"] in AUTO_KNOWLEDGE_TYPES
                    and bool(row["verified"])]
        query_counts = Counter(terms)
        document_terms = [json.loads(row["terms_json"]) for row in rows]
        document_frequency = Counter(token for tokens in document_terms for token in set(tokens))
        average_length = (sum(map(len, document_terms)) / len(document_terms)) if rows else 1.0
        ranked = []
        for row, tokens in zip(rows, document_terms):
            counts = Counter(tokens)
            score = 0.0
            for term, query_weight in query_counts.items():
                frequency = counts.get(term, 0)
                if not frequency:
                    continue
                inverse = math.log(1 + (len(rows) - document_frequency[term] + 0.5)
                                   / (document_frequency[term] + 0.5))
                denominator = frequency + 1.2 * (0.25 + 0.75 * len(tokens) / average_length)
                score += query_weight * inverse * frequency * 2.2 / denominator
            tag_tokens = set(_tokens(" ".join(json.loads(row["tags_json"]))))
            score += 0.25 * len(set(terms) & tag_tokens)
            if score <= 0:
                continue
            ranked.append({
                "record_id": row["record_id"], "claim": row["claim"],
                "knowledge_type": row["knowledge_type"],
                "eligible_for_proposal": bool(row["verified"])
                and row["knowledge_type"] in AUTO_KNOWLEDGE_TYPES,
                "scope": row["scope"], "score": round(score, 8),
                "evidence": {"ref": row["evidence_ref"],
                             "sha256": row["evidence_sha256"]},
                "record_fingerprint": row["fingerprint"],
            })
        ranked.sort(key=lambda item: (-item["score"], item["record_id"]))
        selected = tuple(ranked[:limit])
        payload = {"query": query, "context_fingerprint": context.fingerprint,
                   "records": selected}
        return EvidenceBundle(query, context.fingerprint, selected,
                              _fingerprint(payload), False)

    def replay(self, bundle: EvidenceBundle, context: LearningContext) -> EvidenceBundle:
        context.validate()
        if bundle.execution_allowed:
            raise ValueError("EvidenceBundle cannot be executable")
        if bundle.context_fingerprint != context.fingerprint:
            raise ValueError("Evidence bundle context mismatch")
        expected = _fingerprint({"query": bundle.query,
                                 "context_fingerprint": bundle.context_fingerprint,
                                 "records": bundle.records})
        if expected != bundle.bundle_fingerprint:
            raise ValueError("Evidence bundle fingerprint is stale or tampered")
        with self._connect() as connection:
            for item in bundle.records:
                row = connection.execute(
                    "SELECT fingerprint, evidence_ref, evidence_sha256 FROM evidence_knowledge_v2 WHERE record_id = ?",
                    (item["record_id"],),
                ).fetchone()
                if row is None or row["fingerprint"] != item["record_fingerprint"]:
                    raise ValueError("Evidence record is missing or stale")
                if (row["evidence_ref"], row["evidence_sha256"]) != (
                    item["evidence"]["ref"], item["evidence"]["sha256"],
                ):
                    raise ValueError("Evidence citation changed")
        return bundle

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection
