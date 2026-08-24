"""Durable SpecIR, verification-package and RTL-candidate lineage store."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from openroad_platform_contracts import RTLCandidate, SpecIR, VerificationPackage


class RTLFrontendStore:
    """Append-only records for the RTLScout-v2 control plane.

    Candidate checks are append-only as well: a later check never overwrites a
    prior failure or success, which is necessary for audit and evolution.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS rtl_specs_v1 (
                    spec_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rtl_verification_packages_v1 (
                    verification_id TEXT PRIMARY KEY, spec_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(spec_id) REFERENCES rtl_specs_v1(spec_id)
                );
                CREATE TABLE IF NOT EXISTS rtl_candidates_v1 (
                    candidate_id TEXT PRIMARY KEY, spec_id TEXT NOT NULL,
                    verification_id TEXT NOT NULL, payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(spec_id) REFERENCES rtl_specs_v1(spec_id),
                    FOREIGN KEY(verification_id) REFERENCES rtl_verification_packages_v1(verification_id)
                );
                CREATE TABLE IF NOT EXISTS rtl_candidate_checks_v1 (
                    check_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL,
                    check_kind TEXT NOT NULL, status TEXT NOT NULL,
                    evidence_ref TEXT NOT NULL, evidence_sha256 TEXT NOT NULL,
                    detail_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(candidate_id) REFERENCES rtl_candidates_v1(candidate_id),
                    CHECK(status IN ('passed', 'failed', 'skipped'))
                );
                CREATE INDEX IF NOT EXISTS idx_rtl_candidates_spec ON rtl_candidates_v1(spec_id);
                CREATE INDEX IF NOT EXISTS idx_rtl_candidate_checks ON rtl_candidate_checks_v1(candidate_id, check_kind);
            """)

    def add_spec(self, spec: SpecIR) -> str:
        spec.validate()
        self._insert_payload("rtl_specs_v1", "spec_id", spec.spec_id, spec.to_dict())
        return spec.spec_id

    def add_verification_package(self, package: VerificationPackage) -> str:
        package.validate()
        self._require("rtl_specs_v1", "spec_id", package.spec_id)
        self._insert_payload("rtl_verification_packages_v1", "verification_id",
                             package.verification_id, package.to_dict(), spec_id=package.spec_id)
        return package.verification_id

    def add_candidate(self, candidate: RTLCandidate) -> str:
        candidate.validate()
        self._require("rtl_specs_v1", "spec_id", candidate.spec_id)
        package = self.get_verification_package(candidate.verification_id)
        if package.spec_id != candidate.spec_id:
            raise ValueError("RTLCandidate verification package belongs to another SpecIR")
        self._insert_payload("rtl_candidates_v1", "candidate_id", candidate.candidate_id,
                             candidate.to_dict(), spec_id=candidate.spec_id,
                             verification_id=candidate.verification_id)
        return candidate.candidate_id

    def add_check(self, *, check_id: str, candidate_id: str, check_kind: str,
                  status: str, evidence_ref: str, evidence_sha256: str,
                  detail: dict | None = None) -> None:
        if not check_id or not candidate_id or not check_kind:
            raise ValueError("check_id, candidate_id and check_kind are required")
        if status not in {"passed", "failed", "skipped"}:
            raise ValueError("invalid RTL candidate check status")
        if not evidence_ref.startswith(("artifact:", "source:")) or not _sha256(evidence_sha256):
            raise ValueError("candidate check requires a durable evidence pointer")
        self._require("rtl_candidates_v1", "candidate_id", candidate_id)
        with self._connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO rtl_candidate_checks_v1
                       (check_id, candidate_id, check_kind, status, evidence_ref, evidence_sha256,
                        detail_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (check_id, candidate_id, check_kind, status, evidence_ref, evidence_sha256,
                     json.dumps(detail or {}, ensure_ascii=False, sort_keys=True), _now()),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("candidate check already exists") from exc

    def get_spec(self, spec_id: str) -> SpecIR:
        return SpecIR.from_dict(self._payload("rtl_specs_v1", "spec_id", spec_id))

    def get_verification_package(self, verification_id: str) -> VerificationPackage:
        return VerificationPackage.from_dict(self._payload(
            "rtl_verification_packages_v1", "verification_id", verification_id))

    def lineage(self, spec_id: str) -> dict:
        spec = self.get_spec(spec_id)
        with self._connect() as connection:
            packages = connection.execute(
                "SELECT payload_json FROM rtl_verification_packages_v1 WHERE spec_id = ? ORDER BY created_at",
                (spec_id,),
            ).fetchall()
            candidates = connection.execute(
                "SELECT candidate_id, payload_json FROM rtl_candidates_v1 WHERE spec_id = ? ORDER BY created_at",
                (spec_id,),
            ).fetchall()
            checks = connection.execute(
                """SELECT * FROM rtl_candidate_checks_v1 WHERE candidate_id IN
                   (SELECT candidate_id FROM rtl_candidates_v1 WHERE spec_id = ?)
                   ORDER BY created_at""", (spec_id,),
            ).fetchall()
        return {
            "spec": spec.to_dict(),
            "verification_packages": [VerificationPackage.from_dict(json.loads(row["payload_json"])).to_dict()
                                      for row in packages],
            "candidates": [RTLCandidate.from_dict(json.loads(row["payload_json"])).to_dict()
                           for row in candidates],
            "checks": [{**dict(row), "detail": json.loads(row["detail_json"])} for row in checks],
        }

    def _insert_payload(self, table: str, key: str, value: str, payload: dict,
                        **columns: str) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        names = [key, *columns, "payload_json", "payload_sha256", "created_at"]
        values = [value, *columns.values(), encoded, digest, _now()]
        with self._connect() as connection:
            try:
                connection.execute(
                    f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})",
                    values,
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"{key} already exists; frontend records are immutable") from exc

    def _payload(self, table: str, key: str, value: str) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT payload_json, payload_sha256 FROM {table} WHERE {key} = ?", (value,)
            ).fetchone()
        if row is None:
            raise KeyError(value)
        if hashlib.sha256(row["payload_json"].encode("utf-8")).hexdigest() != row["payload_sha256"]:
            raise RuntimeError("RTL frontend payload integrity check failed")
        return json.loads(row["payload_json"])

    def _require(self, table: str, key: str, value: str) -> None:
        self._payload(table, key, value)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection


def _sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
