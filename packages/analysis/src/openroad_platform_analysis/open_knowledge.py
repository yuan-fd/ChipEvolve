"""Traceable public knowledge and benchmark registry.

External claims are deliberately kept outside the observed Runtime dataset.
The registry accepts only license-audited, hash-pinned content and applies
context filters before text ranking.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping


SHA256 = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
PUBLIC_KINDS = {"official_documentation", "paper_derived_claim",
                "upstream_benchmark_metadata", "bibliographic_metadata"}
LICENSE_DECISIONS = {"redistributable", "metadata_only", "restricted", "rejected"}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[A-Za-z0-9_.:+-]+|[\u4e00-\u9fff]{2,}", value.lower()))


@dataclass(frozen=True)
class KnowledgeSource:
    source_id: str
    title: str
    organization: str
    url: str
    version: str
    license_id: str
    license_decision: str
    acquired_at: str
    content_sha256: str
    content_kind: str
    redistributable: bool
    hash_basis: str = ""
    notes: str = ""
    authors: tuple[str, ...] = ()
    venue: str = ""
    year: int | None = None
    doi: str = ""
    arxiv_id: str = ""
    hash_input: str = ""

    def validate(self) -> None:
        if not IDENTIFIER.fullmatch(self.source_id):
            raise ValueError("Invalid knowledge source_id")
        if not self.title.strip() or not self.organization.strip():
            raise ValueError("Knowledge source title and organization are required")
        if not self.url.startswith(("https://", "http://")):
            raise ValueError("Knowledge source URL must be HTTP(S)")
        if not self.version.strip() or not self.license_id.strip():
            raise ValueError("Knowledge source version and license are required")
        if self.license_decision not in LICENSE_DECISIONS:
            raise ValueError("Invalid license decision")
        if not SHA256.fullmatch(self.content_sha256):
            raise ValueError("Invalid source content SHA-256")
        # Old lockfiles stored the exact hash input in ``hash_basis``.  New
        # bibliographic entries derive it from typed metadata, so changing a
        # title, author list, venue, year, DOI or arXiv version invalidates the
        # pin rather than silently retaining a label-only hash.
        if self.content_kind == "bibliographic_metadata":
            author_text = (f"{self.authors[0]} et al." if len(self.authors) > 5
                           else ";".join(self.authors))
            venue_text = "arXiv" if self.venue == "arXiv preprint" else self.venue
            pinned_input = "|".join((self.title, author_text, venue_text,
                                     str(self.year or ""), self.doi or self.arxiv_id))
        else:
            pinned_input = self.hash_input or self.hash_basis
        if (pinned_input and
                hashlib.sha256(pinned_input.encode()).hexdigest() != self.content_sha256):
            raise ValueError("Source content SHA-256 does not match its pinned hash basis")
        if self.content_kind not in PUBLIC_KINDS:
            raise ValueError("External source cannot use observed_fact")
        if self.redistributable and self.license_decision != "redistributable":
            raise ValueError("Only redistributable sources may cache content")
        if self.year is not None and not 1900 <= self.year <= 2100:
            raise ValueError("Invalid bibliographic year")
        if self.doi and not self.doi.startswith("10."):
            raise ValueError("Invalid DOI")
        if self.arxiv_id and not re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", self.arxiv_id):
            raise ValueError("Invalid arXiv identifier")
        if any(not author.strip() for author in self.authors):
            raise ValueError("Invalid author list")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class DocumentClaim:
    claim_id: str
    source_id: str
    locator: str
    text: str
    evidence_level: str
    platforms: tuple[str, ...] = ()
    toolchains: tuple[str, ...] = ()
    stages: tuple[str, ...] = ()
    design_classes: tuple[str, ...] = ()
    prompt_injection_reviewed: bool = False

    def validate(self) -> None:
        if not IDENTIFIER.fullmatch(self.claim_id) or not IDENTIFIER.fullmatch(self.source_id):
            raise ValueError("Invalid claim identity")
        if not self.locator.strip() or not self.text.strip() or len(self.text) > 16_000:
            raise ValueError("Invalid claim content")
        if self.evidence_level not in {"official", "peer_reviewed", "preprint",
                                       "artifact", "metadata"}:
            raise ValueError("Invalid claim evidence level")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class BenchmarkDefinition:
    benchmark_id: str
    source_id: str
    title: str
    version: str
    license_id: str
    design_names: tuple[str, ...]
    entrypoint: str
    allowed_platforms: tuple[str, ...]
    rtl_sha256: str | None = None
    constraints_sha256: str | None = None
    local_observation_eligible: bool = False

    def validate(self) -> None:
        if not IDENTIFIER.fullmatch(self.benchmark_id) or not IDENTIFIER.fullmatch(self.source_id):
            raise ValueError("Invalid benchmark identity")
        if not self.title.strip() or not self.version.strip() or not self.license_id.strip():
            raise ValueError("Benchmark title, version and license are required")
        if not self.design_names or not self.entrypoint.strip() or not self.allowed_platforms:
            raise ValueError("Benchmark design, entrypoint and platform are required")
        for value in (self.rtl_sha256, self.constraints_sha256):
            if value is not None and not SHA256.fullmatch(value):
                raise ValueError("Invalid benchmark payload SHA-256")
        if self.local_observation_eligible:
            raise ValueError("External benchmark results are not local observations")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class CorpusSnapshot:
    snapshot_id: str
    source_ids: tuple[str, ...]
    source_hashes: tuple[str, ...]
    parser_version: str
    chunker_version: str
    embedding_version: str
    reranker_version: str
    manifest_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "manifest_sha256", _digest({
            "source_ids": self.source_ids, "source_hashes": self.source_hashes,
            "parser_version": self.parser_version, "chunker_version": self.chunker_version,
            "embedding_version": self.embedding_version,
            "reranker_version": self.reranker_version,
        }))

    def validate(self) -> None:
        if not IDENTIFIER.fullmatch(self.snapshot_id) or not self.source_ids:
            raise ValueError("Invalid corpus snapshot")
        if len(self.source_ids) != len(self.source_hashes):
            raise ValueError("Snapshot sources and hashes differ")
        if not all(SHA256.fullmatch(item) for item in self.source_hashes):
            raise ValueError("Invalid snapshot source hash")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return dataclasses.asdict(self)


class PublicKnowledgeRegistry:
    """SQLite registry for external, non-observed evidence."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS public_sources_v1 (
                    source_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS public_claims_v1 (
                    claim_id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
                    text_value TEXT NOT NULL, terms_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE,
                    FOREIGN KEY(source_id) REFERENCES public_sources_v1(source_id)
                );
                CREATE TABLE IF NOT EXISTS public_benchmarks_v1 (
                    benchmark_id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE,
                    FOREIGN KEY(source_id) REFERENCES public_sources_v1(source_id)
                );
                CREATE TABLE IF NOT EXISTS corpus_snapshots_v1 (
                    snapshot_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
                    manifest_sha256 TEXT NOT NULL UNIQUE
                );
            """)

    def import_manifest(self, manifest: Mapping[str, Any]) -> CorpusSnapshot:
        sources = [KnowledgeSource(**{**item, "authors": tuple(item.get("authors", ()))})
                   for item in manifest.get("sources", ())]
        claims = [DocumentClaim(**{**item, **{
            key: tuple(item.get(key, ())) for key in
            ("platforms", "toolchains", "stages", "design_classes")
        }}) for item in manifest.get("claims", ())]
        benchmarks = [BenchmarkDefinition(**{**item,
            "design_names": tuple(item.get("design_names", ())),
            "allowed_platforms": tuple(item.get("allowed_platforms", ()))})
            for item in manifest.get("benchmarks", ())]
        for source in sources:
            self.add_source(source)
        for claim in claims:
            self.add_claim(claim)
        for benchmark in benchmarks:
            self.add_benchmark(benchmark)
        snapshot_data = dict(manifest.get("snapshot") or {})
        snapshot = CorpusSnapshot(
            snapshot_id=snapshot_data["snapshot_id"],
            source_ids=tuple(item.source_id for item in sources),
            source_hashes=tuple(item.content_sha256 for item in sources),
            parser_version=snapshot_data["parser_version"],
            chunker_version=snapshot_data["chunker_version"],
            embedding_version=snapshot_data["embedding_version"],
            reranker_version=snapshot_data["reranker_version"],
        )
        self.add_snapshot(snapshot)
        return snapshot

    def add_source(self, item: KnowledgeSource) -> None:
        item.validate()
        payload = item.to_dict()
        self._insert_four("public_sources_v1", "source_id", item.source_id,
                          payload, item.content_sha256, _digest(payload))

    def add_claim(self, item: DocumentClaim) -> None:
        item.validate()
        with self._connect() as connection:
            if connection.execute("SELECT 1 FROM public_sources_v1 WHERE source_id = ?",
                                  (item.source_id,)).fetchone() is None:
                raise ValueError("Claim source is not registered")
            payload = item.to_dict()
            fingerprint = _digest(payload)
            try:
                connection.execute("INSERT INTO public_claims_v1 VALUES (?, ?, ?, ?, ?, ?)",
                    (item.claim_id, item.source_id, item.text,
                     json.dumps(sorted(_tokens(item.text))), json.dumps(payload, ensure_ascii=False),
                     fingerprint))
            except sqlite3.IntegrityError:
                row = connection.execute("SELECT fingerprint FROM public_claims_v1 WHERE claim_id = ?",
                                         (item.claim_id,)).fetchone()
                if row is None or row[0] != fingerprint:
                    raise ValueError("Claim identity conflict")

    def add_benchmark(self, item: BenchmarkDefinition) -> None:
        item.validate()
        self._require_source(item.source_id)
        payload = item.to_dict()
        fingerprint = _digest(payload)
        with self._connect() as connection:
            try:
                connection.execute("INSERT INTO public_benchmarks_v1 VALUES (?, ?, ?, ?)",
                    (item.benchmark_id, item.source_id,
                     json.dumps(payload, ensure_ascii=False), fingerprint))
            except sqlite3.IntegrityError:
                row = connection.execute(
                    "SELECT payload_json FROM public_benchmarks_v1 WHERE benchmark_id = ?",
                    (item.benchmark_id,)).fetchone()
                if row is None or _digest(json.loads(row[0])) != fingerprint:
                    raise ValueError("Benchmark identity conflict")

    def add_snapshot(self, item: CorpusSnapshot) -> None:
        item.validate()
        self._insert_identity("corpus_snapshots_v1", "snapshot_id", item.snapshot_id,
                              item.to_dict(), item.manifest_sha256,
                              fingerprint_column="manifest_sha256")

    def search(self, query: str, *, platform: str, toolchain: str, stage: str,
               design_class: str = "", limit: int = 10) -> list[dict[str, Any]]:
        if not query.strip() or not all(isinstance(item, str) for item in
                                       (platform, toolchain, stage, design_class)):
            raise ValueError("Search query and context are required")
        terms = _tokens(query)
        with self._connect() as connection:
            rows = connection.execute("""SELECT c.payload_json, s.payload_json
                FROM public_claims_v1 c JOIN public_sources_v1 s USING(source_id)""").fetchall()
        matches = []
        for claim_raw, source_raw in rows:
            claim = json.loads(claim_raw)
            source = json.loads(source_raw)
            if source["license_decision"] in {"restricted", "rejected"}:
                continue
            if not claim.get("prompt_injection_reviewed"):
                continue
            filters = (("platforms", platform), ("toolchains", toolchain),
                       ("stages", stage), ("design_classes", design_class))
            if any(claim.get(key) and value not in claim[key] for key, value in filters):
                continue
            score = len(terms & _tokens(claim["text"]))
            if score:
                matches.append({"claim": claim, "source": source, "score": score,
                                "knowledge_origin": "external_public",
                                "local_observation": False})
        matches.sort(key=lambda item: (-item["score"], item["claim"]["claim_id"]))
        return matches[:max(1, min(int(limit), 50))]

    def list_sources(self) -> list[dict[str, Any]]:
        return self._payloads("public_sources_v1", "source_id")

    def list_benchmarks(self) -> list[dict[str, Any]]:
        return self._payloads("public_benchmarks_v1", "benchmark_id")

    def verify_manifest(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        snapshot = self.import_manifest(manifest)
        return {"snapshot": snapshot.to_dict(), "source_count": len(self.list_sources()),
                "benchmark_count": len(self.list_benchmarks()),
                "external_results_observed": False}

    def _insert_identity(self, table: str, id_column: str, identity: str,
                         payload: Mapping[str, Any], value: str, *,
                         fingerprint_column: str = "fingerprint") -> None:
        fingerprint = _digest(payload)
        with self._connect() as connection:
            try:
                connection.execute(f"INSERT INTO {table} VALUES (?, ?, ?)",
                                   (identity, json.dumps(payload, ensure_ascii=False), value))
            except sqlite3.IntegrityError:
                row = connection.execute(
                    f"SELECT payload_json FROM {table} WHERE {id_column} = ?", (identity,)
                ).fetchone()
                if row is None or _digest(json.loads(row[0])) != fingerprint:
                    raise ValueError(f"{table} identity conflict")

    def _insert_four(self, table: str, id_column: str, identity: str,
                     payload: Mapping[str, Any], third: str, fourth: str) -> None:
        fingerprint = _digest(payload)
        with self._connect() as connection:
            try:
                connection.execute(f"INSERT INTO {table} VALUES (?, ?, ?, ?)",
                                   (identity, json.dumps(payload, ensure_ascii=False),
                                    third, fourth))
            except sqlite3.IntegrityError:
                row = connection.execute(
                    f"SELECT payload_json FROM {table} WHERE {id_column} = ?", (identity,)
                ).fetchone()
                if row is None or _digest(json.loads(row[0])) != fingerprint:
                    raise ValueError(f"{table} identity conflict")

    def _require_source(self, source_id: str) -> None:
        with self._connect() as connection:
            if connection.execute("SELECT 1 FROM public_sources_v1 WHERE source_id = ?",
                                  (source_id,)).fetchone() is None:
                raise ValueError("Benchmark source is not registered")

    def _payloads(self, table: str, order: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return [json.loads(row[0]) for row in connection.execute(
                f"SELECT payload_json FROM {table} ORDER BY {order}").fetchall()]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def load_public_manifest(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Knowledge manifest must be an object")
    return value
