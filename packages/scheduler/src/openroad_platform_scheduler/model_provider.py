"""BYOK model profiles and an in-memory secret broker.

Only non-secret profile data is durable. Secret values never enter SQLite,
Runtime, artifacts, logs, subprocess environments or exception messages.
"""

from __future__ import annotations

import dataclasses
import ipaddress
import json
import secrets
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .spec_conversation import SpecProposal


@dataclass(frozen=True)
class ProviderProfile:
    profile_id: str
    owner_id: str
    provider_type: str
    base_url: str
    model: str
    timeout_seconds: int = 60
    max_response_bytes: int = 1_048_576
    max_calls: int = 8
    allow_private_endpoint: bool = False

    def validate(self) -> None:
        if self.provider_type != "openai-compatible-byok":
            raise ValueError("Unsupported provider profile type")
        if not self.profile_id or not self.owner_id or not self.model.strip():
            raise ValueError("Provider profile identity and model are required")
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise ValueError("Provider base_url must be HTTP(S)")
        if parsed.username or parsed.password or parsed.fragment or parsed.query:
            raise ValueError("Provider base_url cannot contain credentials, query or fragment")
        if parsed.scheme == "http" and not _loopback_name(parsed.hostname):
            raise ValueError("Non-loopback Provider endpoints require HTTPS")
        if not 1 <= self.timeout_seconds <= 300:
            raise ValueError("Provider timeout must be 1-300 seconds")
        if not 1024 <= self.max_response_bytes <= 4 * 1024 * 1024:
            raise ValueError("Provider response limit must be 1 KiB-4 MiB")
        if not 1 <= self.max_calls <= 100:
            raise ValueError("Provider call budget must be 1-100")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return dataclasses.asdict(self)


@dataclass
class _Secret:
    value: bytearray
    owner_id: str
    session_id: str
    purpose: str
    expires_at: float


class InMemorySecretBroker:
    """Process-local capability handles with owner/session binding and TTL."""

    def __init__(self, *, default_ttl_seconds: int = 8 * 3600,
                 clock=time.monotonic):
        if not 1 <= default_ttl_seconds <= 24 * 3600:
            raise ValueError("Secret TTL must be 1 second-24 hours")
        self.default_ttl_seconds = default_ttl_seconds
        self._clock = clock
        self._items: dict[str, _Secret] = {}
        self._lock = threading.RLock()

    def put(self, value: str, *, owner_id: str, session_id: str,
            purpose: str = "model-api", ttl_seconds: int | None = None) -> str:
        if not isinstance(value, str) or not value or len(value.encode()) > 16_384:
            raise ValueError("API key is empty or too large")
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl_seconds
        if not 1 <= ttl <= 24 * 3600 or not owner_id or not session_id:
            raise ValueError("Invalid secret owner, session or TTL")
        handle = f"secret-{secrets.token_urlsafe(24)}"
        with self._lock:
            self._items[handle] = _Secret(bytearray(value.encode()), owner_id, session_id,
                                          purpose, self._clock() + ttl)
        return handle

    def resolve(self, handle: str, *, owner_id: str, session_id: str,
                purpose: str = "model-api") -> str:
        with self._lock:
            item = self._items.get(handle)
            if item is None or item.expires_at <= self._clock():
                if item is not None:
                    self._erase(handle, item)
                raise KeyError("Secret handle is missing or expired")
            if (item.owner_id, item.session_id, item.purpose) != (owner_id, session_id, purpose):
                raise PermissionError("Secret handle does not belong to this owner/session")
            return bytes(item.value).decode()

    def revoke(self, handle: str, *, owner_id: str, session_id: str) -> bool:
        with self._lock:
            item = self._items.get(handle)
            if item is None:
                return False
            if (item.owner_id, item.session_id) != (owner_id, session_id):
                raise PermissionError("Secret handle does not belong to this owner/session")
            self._erase(handle, item)
            return True

    def revoke_session(self, owner_id: str, session_id: str) -> int:
        with self._lock:
            targets = [(key, item) for key, item in self._items.items()
                       if (item.owner_id, item.session_id) == (owner_id, session_id)]
            for key, item in targets:
                self._erase(key, item)
            return len(targets)

    def describe(self, handle: str, *, owner_id: str, session_id: str) -> dict[str, Any]:
        with self._lock:
            item = self._items.get(handle)
            if item is None or item.expires_at <= self._clock():
                raise KeyError("Secret handle is missing or expired")
            if (item.owner_id, item.session_id) != (owner_id, session_id):
                raise PermissionError("Secret handle does not belong to this owner/session")
            return {"handle": handle, "purpose": item.purpose,
                    "expires_in_seconds": max(0, int(item.expires_at - self._clock())),
                    "secret_present": True}

    def _erase(self, handle: str, item: _Secret) -> None:
        for index in range(len(item.value)):
            item.value[index] = 0
        self._items.pop(handle, None)


class ProviderProfileStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS provider_profiles_v1 (
                profile_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                payload_json TEXT NOT NULL, call_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")

    def save(self, profile: ProviderProfile) -> str:
        profile.validate()
        payload = json.dumps(profile.to_dict(), ensure_ascii=False)
        with self._connect() as connection:
            existing = connection.execute("SELECT owner_id FROM provider_profiles_v1 WHERE profile_id = ?",
                                          (profile.profile_id,)).fetchone()
            if existing and existing[0] != profile.owner_id:
                raise PermissionError("Provider profile belongs to another owner")
            connection.execute("""INSERT INTO provider_profiles_v1 VALUES
                (?, ?, ?, 0, datetime('now'), datetime('now'))
                ON CONFLICT(profile_id) DO UPDATE SET payload_json=excluded.payload_json,
                updated_at=datetime('now')""", (profile.profile_id, profile.owner_id, payload))
        return profile.profile_id

    def get(self, profile_id: str, *, owner_id: str) -> ProviderProfile:
        with self._connect() as connection:
            row = connection.execute("""SELECT payload_json FROM provider_profiles_v1
                WHERE profile_id = ? AND owner_id = ?""", (profile_id, owner_id)).fetchone()
        if row is None:
            raise KeyError("Unknown Provider profile")
        profile = ProviderProfile(**json.loads(row[0]))
        profile.validate()
        return profile

    def list(self, *, owner_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("""SELECT payload_json, call_count FROM provider_profiles_v1
                WHERE owner_id = ? ORDER BY profile_id""", (owner_id,)).fetchall()
        return [{**json.loads(row[0]), "call_count": row[1], "api_key_configured": False}
                for row in rows]

    def consume_call(self, profile_id: str, *, owner_id: str) -> None:
        profile = self.get(profile_id, owner_id=owner_id)
        with self._connect() as connection:
            row = connection.execute("""SELECT call_count FROM provider_profiles_v1
                WHERE profile_id = ? AND owner_id = ?""", (profile_id, owner_id)).fetchone()
            if row is None or row[0] >= profile.max_calls:
                raise ValueError("Provider call budget is exhausted")
            connection.execute("""UPDATE provider_profiles_v1 SET call_count=call_count+1,
                updated_at=datetime('now') WHERE profile_id = ? AND owner_id = ?""",
                (profile_id, owner_id))

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise urllib.error.HTTPError(req.full_url, code, "Redirects are disabled", headers, fp)


class OpenAICompatibleSpecProvider:
    provider_name = "openai-compatible-byok"

    def __init__(self, profile: ProviderProfile, secret_broker: InMemorySecretBroker,
                 secret_handle: str, *, owner_id: str, session_id: str,
                 profile_store: ProviderProfileStore | None = None,
                 cancel_event: threading.Event | None = None):
        profile.validate()
        if profile.owner_id != owner_id:
            raise PermissionError("Provider profile belongs to another owner")
        self.profile = profile
        self.model = profile.model
        self.secret_broker = secret_broker
        self.secret_handle = secret_handle
        self.owner_id = owner_id
        self.session_id = session_id
        self.profile_store = profile_store
        self.cancel_event = cancel_event

    def propose(self, messages: Sequence[Mapping[str, str]], current: Mapping[str, Any],
                *, design_context: Mapping[str, Any] | None = None) -> SpecProposal:
        if self.cancel_event and self.cancel_event.is_set():
            raise RuntimeError("Provider request cancelled")
        _validate_endpoint(self.profile)
        api_key = self.secret_broker.resolve(self.secret_handle, owner_id=self.owner_id,
                                             session_id=self.session_id)
        if self.profile_store is not None:
            self.profile_store.consume_call(self.profile.profile_id, owner_id=self.owner_id)
        payload = {
            "model": self.profile.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content":
                "Return one conservative ASIC SpecProposal JSON object only, as a FLAT object with EXACTLY these keys: "
                "objective (string), functionality (string), top (string or null), clock (string or null), "
                "reset (string or null), target_platform (one of nangate45/sky130hd/sky130hs/asap7/gf180), target_stage (one of synth/floorplan/place/cts/route/finish), "
                "clock_period_ns (number), core_utilization_pct (number), place_density (number), "
                "missing_fields (array of strings), assumptions (array of strings), "
                "clarification_questions (array of strings), ready_for_execution (boolean). "
                "Do NOT wrap the object under another key such as \"spec\". Never generate RTL source or invoke tools. "
                "Use only the listed platforms and synth/floorplan/place/cts/route/finish."},
                {"role": "user", "content": json.dumps({
                    "conversation": list(messages), "current": dict(current),
                    "design_context": dict(design_context or {})}, ensure_ascii=False)}],
        }
        base = self.profile.base_url.rstrip("/")
        url = base if base.endswith("/chat/completions") else base + "/chat/completions"
        request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
            headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json",
                     "Accept": "application/json", "User-Agent": "openroad-platform-byok/1"})
        opener = urllib.request.build_opener(_NoRedirect())
        try:
            with opener.open(request, timeout=self.profile.timeout_seconds) as response:
                raw = response.read(self.profile.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Provider HTTP error {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            raise RuntimeError("Provider request failed or timed out") from None
        finally:
            secret_bytes = api_key.encode()
            api_key = ""  # Drop the last ordinary-string reference promptly.
        if self.cancel_event and self.cancel_event.is_set():
            raise RuntimeError("Provider request cancelled")
        if len(raw) > self.profile.max_response_bytes:
            raise RuntimeError("Provider response exceeds configured size limit")
        if secret_bytes and secret_bytes in raw:
            raise RuntimeError("Provider response contained secret material")
        try:
            outer = json.loads(raw)
            content = outer["choices"][0]["message"]["content"]
            value = json.loads(content) if isinstance(content, str) else content
            if not isinstance(value, dict):
                raise TypeError
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError):
            raise RuntimeError("Provider returned invalid structured JSON") from None
        return SpecProposal.from_mapping(value)


def _loopback_name(host: str) -> bool:
    return host.lower() == "localhost" or host.lower().endswith(".localhost") or host in {
        "127.0.0.1", "::1"}


def _validate_endpoint(profile: ProviderProfile) -> None:
    parsed = urllib.parse.urlparse(profile.base_url)
    host = parsed.hostname or ""
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or
                     (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except socket.gaierror:
        raise ValueError("Provider endpoint cannot be resolved") from None
    if not addresses:
        raise ValueError("Provider endpoint has no address")
    unsafe = []
    for value in addresses:
        ip = ipaddress.ip_address(value)
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified):
            unsafe.append(value)
    if unsafe and not profile.allow_private_endpoint:
        raise ValueError("Provider endpoint resolves to a non-public address")
    if profile.allow_private_endpoint and not _loopback_name(host):
        raise ValueError("Private Provider endpoints are limited to explicit loopback hosts")
