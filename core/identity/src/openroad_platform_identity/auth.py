"""Users, sessions, resource ownership, and per-feature allowances.

Ported from the frozen v1 `AuthStore`, behaviour preserved.  The parts worth
keeping are the ones that are easy to get subtly wrong:

* **Timing equalisation.**  A login for a username that does not exist still
  performs a full PBKDF2 derivation before failing.  Without it, an unknown
  user returns in microseconds while a real one takes ~310k iterations, and the
  response time tells an attacker which usernames exist.
* **Constant-time comparison.**  Password digests are compared with
  ``hmac.compare_digest``, never ``==``.
* **First user bootstrap.**  The first account registered becomes the developer
  and is granted legacy access; later accounts are ordinary members.  This is
  how a fresh deployment gets an administrator without a config file.
* **Opaque, hashed session tokens.**  The token is random and only its SHA-256
  is stored, so a database leak does not yield usable sessions.

Schema note: this is a *fresh* database, so the tables are created with the
final shape and there is no migration path from v1's `web_*_v1` tables.  Pointing
this at a v1 auth database fails loudly on the missing columns rather than
silently reading a half-understood schema.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

#: 3-32 characters, starting alphanumeric.
USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,31}$")

#: PBKDF2-HMAC-SHA256 work factor.
PBKDF2_ITERATIONS = 310_000

#: Browser sessions last a week.
SESSION_SECONDS = 7 * 24 * 3600

#: Salt for the timing-equalisation derivation.  Not a credential.
_DUMMY_SALT = b"openroad-platform"

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256

_SCHEMA = """
CREATE TABLE IF NOT EXISTS identity_users (
    user_id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_salt BLOB NOT NULL,
    password_hash BLOB NOT NULL,
    password_iterations INTEGER NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS identity_sessions (
    token_hash TEXT PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    FOREIGN KEY(user_id) REFERENCES identity_users(user_id)
);
CREATE INDEX IF NOT EXISTS idx_identity_sessions_user
    ON identity_sessions(user_id, expires_at);
CREATE TABLE IF NOT EXISTS identity_resource_owners (
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY(resource_type, resource_id),
    FOREIGN KEY(user_id) REFERENCES identity_users(user_id)
);
CREATE TABLE IF NOT EXISTS identity_feature_usage (
    user_id TEXT NOT NULL,
    feature TEXT NOT NULL,
    window_id INTEGER NOT NULL,
    usage_count INTEGER NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(user_id, feature, window_id),
    FOREIGN KEY(user_id) REFERENCES identity_users(user_id)
);
"""


@dataclass(frozen=True)
class AuthSession:
    user_id: str
    username: str
    developer: bool
    session_id: str

    def public(self) -> dict[str, object]:
        """The browser-facing projection.  Contains no token and no hash."""
        return {
            "authenticated": True,
            "user": {
                "id": self.user_id,
                "username": self.username,
                "role": "developer" if self.developer else "member",
            },
            "developer": self.developer,
            "session_id": self.session_id,
        }


class IdentityStore:
    """SQLite users, hashed sessions, resource ownership, and allowances."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    # -- connection -------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        # Journal mode is DELETE, not WAL.  The state root may live on a shared
        # filesystem, where SQLite's WAL coordination is host-local and
        # therefore invalid -- the same reasoning as the runtime store.
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    # -- accounts ---------------------------------------------------------

    def register(self, username: str, password: str) -> tuple[AuthSession, str]:
        normalized = self._username(username)
        self._password(password)
        salt = secrets.token_bytes(16)
        digest = self._derive(password, salt, PBKDF2_ITERATIONS)
        now = time.time()
        user_id = f"user-{uuid.uuid4().hex}"
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            first_user = connection.execute(
                "SELECT COUNT(*) FROM identity_users"
            ).fetchone()[0] == 0
            try:
                connection.execute(
                    "INSERT INTO identity_users (user_id, username, "
                    "password_salt, password_hash, password_iterations, "
                    "role, created_at) VALUES (?,?,?,?,?,?,?)",
                    (user_id, normalized, salt, digest, PBKDF2_ITERATIONS,
                     "developer" if first_user else "member", now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Username is already registered") from exc
        return self._new_session(user_id, normalized, first_user)

    def login(self, username: str, password: str) -> tuple[AuthSession, str]:
        normalized = self._username(username)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT user_id, username, password_salt, password_hash, "
                "password_iterations, role FROM identity_users "
                "WHERE username = ? COLLATE NOCASE",
                (normalized,),
            ).fetchone()
        if row is None:
            # Spend the same work as a real check so the response time does not
            # reveal whether the username exists.
            self._dummy_password_check(password)
            raise ValueError("Invalid username or password")
        candidate = self._derive(password, row[2], int(row[4]))
        if not hmac.compare_digest(candidate, row[3]):
            raise ValueError("Invalid username or password")
        return self._new_session(row[0], row[1], row[5] == "developer")

    def resolve(self, token: str | None) -> AuthSession | None:
        """Return the session for a token, or None.  Expired tokens are removed."""
        if not token:
            return None
        token_hash = self._token_hash(token)
        now = time.time()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT s.session_id, s.user_id, u.username, u.role, "
                "s.expires_at FROM identity_sessions s "
                "JOIN identity_users u USING(user_id) WHERE s.token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None or float(row[4]) <= now:
                if row is not None:
                    connection.execute(
                        "DELETE FROM identity_sessions WHERE token_hash = ?",
                        (token_hash,),
                    )
                return None
            connection.execute(
                "UPDATE identity_sessions SET last_seen_at = ? WHERE token_hash = ?",
                (now, token_hash),
            )
        return AuthSession(row[1], row[2], row[3] == "developer", row[0])

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM identity_sessions WHERE token_hash = ?",
                (self._token_hash(token),),
            )

    def ensure_local_user(self) -> bool:
        """Create the shared no-auth identity if it is missing.

        Resource ownership has a foreign key onto the users table, so the
        no-auth mode still needs a real row to bind against.  Returns True when
        it created the row.
        """
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT user_id FROM identity_users WHERE username = 'local-user'"
            ).fetchone()
            if row is not None:
                return False
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO identity_users (user_id, username, password_salt, "
                "password_hash, password_iterations, role, "
                "created_at) VALUES (?,?,?,?,?,?,?)",
                (f"user-{uuid.uuid4().hex}", "local-user", b"", b"",
                 PBKDF2_ITERATIONS, "developer", time.time()),
            )
        return True

    def has_user(self, user_id: str) -> bool:
        with self._connect() as connection:
            return connection.execute(
                "SELECT 1 FROM identity_users WHERE user_id = ?", (user_id,)
            ).fetchone() is not None

    def list_users(self) -> list[dict[str, object]]:
        """A read model.  Never includes a salt, a hash, or a token."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT user_id, username, role, created_at "
                "FROM identity_users ORDER BY created_at"
            ).fetchall()
        return [
            {"user_id": r[0], "username": r[1], "role": r[2],
             "created_at": r[3]}
            for r in rows
        ]

    # -- resource ownership ----------------------------------------------

    def bind_resource(self, resource_type: str, resource_id: str, user_id: str) -> None:
        """Record the owner of a resource.

        Re-binding the same resource to a different owner is refused rather
        than overwritten: silently reassigning ownership would let a later
        caller take over an earlier caller's experiment.
        """
        self._resource_type(resource_type)
        if not resource_id:
            raise ValueError("resource_id is required")
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT user_id FROM identity_resource_owners "
                "WHERE resource_type = ? AND resource_id = ?",
                (resource_type, resource_id),
            ).fetchone()
            if existing is not None:
                if existing[0] != user_id:
                    raise ValueError(
                        f"{resource_type} {resource_id!r} is already owned by "
                        f"another user"
                    )
                return
            connection.execute(
                "INSERT INTO identity_resource_owners "
                "(resource_type, resource_id, user_id, created_at) VALUES (?,?,?,?)",
                (resource_type, resource_id, user_id, time.time()),
            )

    def owns_resource(self, resource_type: str, resource_id: str, user_id: str,
                      *, developer_all: bool = False) -> bool:
        owner = self.owner_of(resource_type, resource_id)
        if owner is None:
            return True  # unowned resources stay accessible
        if owner == user_id:
            return True
        if not developer_all:
            return False
        with self._connect() as connection:
            row = connection.execute(
                "SELECT role FROM identity_users WHERE user_id = ?", (user_id,)
            ).fetchone()
        return bool(row) and row[0] == "developer"

    def owner_of(self, resource_type: str, resource_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT user_id FROM identity_resource_owners "
                "WHERE resource_type = ? AND resource_id = ?",
                (resource_type, resource_id),
            ).fetchone()
        return row[0] if row else None

    def resources_owned(self, resource_type: str, user_id: str) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT resource_id FROM identity_resource_owners "
                "WHERE resource_type = ? AND user_id = ?",
                (resource_type, user_id),
            ).fetchall()
        return [r[0] for r in rows]

    # -- allowances -------------------------------------------------------

    def consume_allowance(self, user_id: str, feature: str, *, limit: int,
                          window_seconds: int = 86_400) -> tuple[bool, int]:
        """Consume one unit of a fixed-window allowance.

        Returns (allowed, remaining).  The read and the increment happen in one
        transaction, so two concurrent requests cannot both see the last unit as
        available.
        """
        if limit < 1 or window_seconds < 60:
            raise ValueError("invalid feature allowance")
        now = time.time()
        window_id = int(now // window_seconds)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT usage_count FROM identity_feature_usage WHERE "
                "user_id = ? AND feature = ? AND window_id = ?",
                (user_id, feature, window_id),
            ).fetchone()
            used = int(row[0]) if row else 0
            if used >= limit:
                return False, 0
            connection.execute(
                "INSERT INTO identity_feature_usage "
                "(user_id, feature, window_id, usage_count, updated_at) "
                "VALUES (?,?,?,1,?) ON CONFLICT(user_id, feature, window_id) "
                "DO UPDATE SET usage_count = usage_count + 1, "
                "updated_at = excluded.updated_at",
                (user_id, feature, window_id, now),
            )
        return True, limit - used - 1

    # -- internals --------------------------------------------------------

    def _new_session(self, user_id: str, username: str,
                     developer: bool) -> tuple[AuthSession, str]:
        token = secrets.token_urlsafe(32)
        session_id = f"session-{uuid.uuid4().hex}"
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO identity_sessions (token_hash, session_id, "
                "user_id, created_at, expires_at, last_seen_at) VALUES (?,?,?,?,?,?)",
                (self._token_hash(token), session_id, user_id, now,
                 now + SESSION_SECONDS, now),
            )
        return AuthSession(user_id, username, developer, session_id), token

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _username(value: str) -> str:
        username = str(value or "").strip()
        if not USERNAME.fullmatch(username):
            raise ValueError(
                "Username must be 3-32 characters using letters, numbers, "
                "dot, dash, or underscore"
            )
        return username

    @staticmethod
    def _password(value: str) -> None:
        if (not isinstance(value, str)
                or not MIN_PASSWORD_LENGTH <= len(value) <= MAX_PASSWORD_LENGTH):
            raise ValueError(
                f"Password must contain {MIN_PASSWORD_LENGTH}-"
                f"{MAX_PASSWORD_LENGTH} characters"
            )

    @staticmethod
    def _derive(password: str, salt: bytes, iterations: int) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                                   iterations)

    @classmethod
    def _dummy_password_check(cls, password: str) -> None:
        """Burn the same work as a real check, for a user that does not exist."""
        cls._derive(str(password or ""), _DUMMY_SALT, PBKDF2_ITERATIONS)

    @staticmethod
    def _resource_type(value: str) -> None:
        if not isinstance(value, str) or not value or len(value) > 64:
            raise ValueError(f"invalid resource_type: {value!r}")
