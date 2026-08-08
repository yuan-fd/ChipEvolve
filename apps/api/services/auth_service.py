"""Small password/session store for the public review console."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,31}$")
PBKDF2_ITERATIONS = 310_000
SESSION_SECONDS = 7 * 24 * 3600


@dataclass(frozen=True)
class AuthSession:
    user_id: str
    username: str
    legacy_access: bool
    developer: bool
    session_id: str

    def public(self) -> dict[str, object]:
        return {
            "authenticated": True,
            "user": {"id": self.user_id, "username": self.username,
                     "role": "developer" if self.developer else "member"},
            "legacy_access": self.legacy_access,
            "developer": self.developer,
            "session_id": self.session_id,
        }


class AuthStore:
    """SQLite users plus opaque, hashed browser sessions."""

    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS web_users_v1 (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    password_iterations INTEGER NOT NULL,
                    legacy_access INTEGER NOT NULL DEFAULT 0,
                    role TEXT NOT NULL DEFAULT 'member',
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS web_sessions_v1 (
                    token_hash TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL UNIQUE,
                    user_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES web_users_v1(user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_web_sessions_user
                    ON web_sessions_v1(user_id, expires_at);
                CREATE TABLE IF NOT EXISTS web_resource_owners_v1 (
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(resource_type, resource_id),
                    FOREIGN KEY(user_id) REFERENCES web_users_v1(user_id)
                );
                CREATE TABLE IF NOT EXISTS web_feature_usage_v1 (
                    user_id TEXT NOT NULL,
                    feature TEXT NOT NULL,
                    window_id INTEGER NOT NULL,
                    usage_count INTEGER NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(user_id, feature, window_id),
                    FOREIGN KEY(user_id) REFERENCES web_users_v1(user_id)
                );
            """)
            columns = {row[1] for row in connection.execute(
                "PRAGMA table_info(web_users_v1)"
            ).fetchall()}
            if "role" not in columns:
                connection.execute(
                    "ALTER TABLE web_users_v1 ADD COLUMN role TEXT NOT NULL DEFAULT 'member'"
                )
            connection.execute(
                "UPDATE web_users_v1 SET role = 'developer' WHERE legacy_access = 1"
            )

    def register(self, username: str, password: str) -> tuple[AuthSession, str]:
        normalized = self._username(username)
        self._password(password)
        salt = secrets.token_bytes(16)
        digest = self._derive(password, salt, PBKDF2_ITERATIONS)
        now = time.time()
        user_id = f"user-{uuid.uuid4().hex}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            first_user = connection.execute(
                "SELECT COUNT(*) FROM web_users_v1"
            ).fetchone()[0] == 0
            try:
                connection.execute(
                    """INSERT INTO web_users_v1
                       (user_id, username, password_salt, password_hash,
                        password_iterations, legacy_access, role, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (user_id, normalized, salt, digest, PBKDF2_ITERATIONS,
                     int(first_user), "developer" if first_user else "member", now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Username is already registered") from exc
        return self._new_session(user_id, normalized, first_user, first_user)

    def login(self, username: str, password: str) -> tuple[AuthSession, str]:
        normalized = self._username(username)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT user_id, username, password_salt, password_hash,
                          password_iterations, legacy_access, role
                   FROM web_users_v1 WHERE username = ? COLLATE NOCASE""",
                (normalized,),
            ).fetchone()
        if row is None:
            self._dummy_password_check(password)
            raise ValueError("Invalid username or password")
        candidate = self._derive(password, row[2], int(row[4]))
        if not hmac.compare_digest(candidate, row[3]):
            raise ValueError("Invalid username or password")
        return self._new_session(row[0], row[1], bool(row[5]), row[6] == "developer")

    def resolve(self, token: str | None) -> AuthSession | None:
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = time.time()
        with self._connect() as connection:
            row = connection.execute(
                """SELECT s.session_id, s.user_id, u.username, u.legacy_access, u.role,
                          s.expires_at
                   FROM web_sessions_v1 s JOIN web_users_v1 u USING(user_id)
                   WHERE s.token_hash = ?""",
                (token_hash,),
            ).fetchone()
            if row is None or float(row[5]) <= now:
                connection.execute(
                    "DELETE FROM web_sessions_v1 WHERE token_hash = ?", (token_hash,)
                )
                return None
            connection.execute(
                "UPDATE web_sessions_v1 SET last_seen_at = ? WHERE token_hash = ?",
                (now, token_hash),
            )
        return AuthSession(row[1], row[2], bool(row[3]), row[4] == "developer", row[0])

    def logout(self, token: str | None) -> None:
        if not token:
            return
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM web_sessions_v1 WHERE token_hash = ?", (token_hash,)
            )

    def bind_resource(self, resource_type: str, resource_id: str, user_id: str) -> None:
        if not resource_type or not resource_id or not user_id:
            raise ValueError("Resource ownership requires type, id, and user")
        with self._connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO web_resource_owners_v1
                       (resource_type, resource_id, user_id, created_at)
                       VALUES (?, ?, ?, ?)""",
                    (resource_type, resource_id, user_id, time.time()),
                )
            except sqlite3.IntegrityError as exc:
                row = connection.execute(
                    """SELECT user_id FROM web_resource_owners_v1
                       WHERE resource_type = ? AND resource_id = ?""",
                    (resource_type, resource_id),
                ).fetchone()
                if row is None or row[0] != user_id:
                    raise PermissionError("Resource belongs to another user") from exc

    def owns_resource(self, resource_type: str, resource_id: str, user_id: str,
                      *, include_legacy: bool = False) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT user_id FROM web_resource_owners_v1
                   WHERE resource_type = ? AND resource_id = ?""",
                (resource_type, resource_id),
            ).fetchone()
        return (row is not None and row[0] == user_id) or (row is None and include_legacy)

    def has_user(self, user_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM web_users_v1 WHERE user_id = ?", (user_id,),
            ).fetchone()
        return row is not None

    def list_users(self) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT user_id, username, role, created_at
                   FROM web_users_v1 ORDER BY created_at"""
            ).fetchall()
        return [
            {"user_id": row[0], "username": row[1], "role": row[2],
             "created_at": row[3]}
            for row in rows
        ]

    def consume_allowance(self, user_id: str, feature: str, *, limit: int,
                          window_seconds: int = 86_400) -> tuple[bool, int]:
        if limit < 1 or window_seconds < 60:
            raise ValueError("Invalid feature allowance")
        now = time.time()
        window_id = int(now // window_seconds)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT usage_count FROM web_feature_usage_v1
                   WHERE user_id = ? AND feature = ? AND window_id = ?""",
                (user_id, feature, window_id),
            ).fetchone()
            used = int(row[0]) if row else 0
            if used >= limit:
                return False, 0
            connection.execute(
                """INSERT INTO web_feature_usage_v1
                   (user_id, feature, window_id, usage_count, updated_at)
                   VALUES (?, ?, ?, 1, ?)
                   ON CONFLICT(user_id, feature, window_id) DO UPDATE SET
                   usage_count = usage_count + 1, updated_at = excluded.updated_at""",
                (user_id, feature, window_id, now),
            )
        return True, limit - used - 1

    def _new_session(self, user_id: str, username: str, legacy_access: bool,
                     developer: bool) -> tuple[AuthSession, str]:
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        session_id = f"session-{uuid.uuid4().hex}"
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO web_sessions_v1
                   (token_hash, session_id, user_id, created_at, expires_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (token_hash, session_id, user_id, now, now + SESSION_SECONDS, now),
            )
        return AuthSession(user_id, username, legacy_access, developer, session_id), token

    @staticmethod
    def _username(value: str) -> str:
        username = str(value or "").strip()
        if not USERNAME.fullmatch(username):
            raise ValueError(
                "Username must be 3-32 characters using letters, numbers, dot, dash, or underscore"
            )
        return username

    @staticmethod
    def _password(value: str) -> None:
        if not isinstance(value, str) or len(value) < 8 or len(value) > 256:
            raise ValueError("Password must contain 8-256 characters")

    @staticmethod
    def _derive(password: str, salt: bytes, iterations: int) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)

    @classmethod
    def _dummy_password_check(cls, password: str) -> None:
        cls._derive(str(password or ""), b"openroad-platform", PBKDF2_ITERATIONS)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
