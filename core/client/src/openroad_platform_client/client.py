"""The typed client an application uses to reach the kernel.

Applications may not open the kernel's database (G5) and may not import kernel
internals (G4).  This is the only door: a versioned HTTP surface described once,
in one place, so an app and the kernel cannot drift into disagreeing about a
field name.

The client is deliberately thin.  It validates that a reply is the shape it
expected and raises on anything else; it does not reinterpret, default, or
repair a response.  A client that guesses turns a kernel bug into a wrong screen
instead of an error.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: How long a call may take.  Submitting a task is fast; reading a large
#: artifact excerpt is bounded server-side, so this only has to cover the
#: round trip and normal queuing.
DEFAULT_TIMEOUT_SECONDS = 30.0

#: The excerpt ceiling the kernel enforces.  Restated here so a caller gets an
#: immediate error instead of a 400 after a round trip.
MAX_EXCERPT_BYTES = 64 * 1024


class KernelError(RuntimeError):
    """The kernel refused the call, or answered something unexpected."""

    def __init__(self, message: str, *, status: int | None = None,
                 body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class KernelUnavailable(KernelError):
    """The kernel could not be reached at all."""


@dataclass
class KernelClient:
    """A session against one kernel."""

    base_url: str
    token: str | None = None
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    _extra_headers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError(f"base_url must be http(s): {self.base_url!r}")
        self.base_url = self.base_url.rstrip("/")

    # -- transport --------------------------------------------------------

    def _call(self, method: str, path: str, *,
              payload: Mapping[str, Any] | None = None,
              query: Mapping[str, Any] | None = None, want_bytes: bool = False) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            cleaned = {
                k: v for k, v in query.items()
                if v is not None and v != "" and v != []
            }
            if cleaned:
                pairs: list[tuple[str, str]] = []
                for key, value in cleaned.items():
                    if isinstance(value, (list, tuple)):
                        pairs.extend((key, str(item)) for item in value)
                    else:
                        pairs.append((key, str(value)))
                url += "?" + urllib.parse.urlencode(pairs)

        headers = {"Accept": "application/json", **self._extra_headers}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body: bytes | None = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=body, headers=headers,
                                         method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise KernelError(
                _error_message(exc), status=exc.code, body=_safe_json(exc.read())
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise KernelUnavailable(
                f"kernel at {self.base_url} is unreachable: {exc}"
            ) from exc

        if not raw:
            return None
        if want_bytes:
            return raw
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise KernelError(
                f"kernel returned a non-JSON body for {method} {path}"
            ) from exc

    # -- identity ---------------------------------------------------------

    def register(self, username: str, password: str) -> dict[str, Any]:
        session = self._call("POST", "/kernel/auth/register",
                             payload={"username": username, "password": password})
        self.token = session["token"]
        return session

    def login(self, username: str, password: str) -> dict[str, Any]:
        session = self._call("POST", "/kernel/auth/login",
                             payload={"username": username, "password": password})
        self.token = session["token"]
        return session

    def session(self) -> dict[str, Any] | None:
        """Who the current token belongs to, or None."""
        reply = self._call("GET", "/kernel/auth/session")
        return reply.get("session")

    def logout(self) -> None:
        self._call("POST", "/kernel/auth/logout")
        self.token = None

    # -- catalogue --------------------------------------------------------

    def health(self) -> dict[str, Any]:
        return self._call("GET", "/kernel/health")

    def plugins(self) -> list[dict[str, Any]]:
        return self._call("GET", "/kernel/plugins")["plugins"]

    # -- runs -------------------------------------------------------------

    def submit(self, task: Mapping[str, Any], *,
               idempotent: bool = False,
               idempotency_key: str | None = None) -> dict[str, Any]:
        """Submit a task.

        ``idempotent`` makes a repeated submission of the same immutable task
        return the existing run instead of creating a second one.  An
        ``idempotency_key`` scopes that guarantee independently of the Agent's
        task id, which lets two plans submit the same task id safely.
        """
        return self._call("POST", "/kernel/runs",
                          payload={"task": dict(task)},
                          query={
                              "idempotent": "1" if idempotent else None,
                              "idempotency_key": idempotency_key,
                          })

    def runs(self, *, project_id: str | None = None, design_id: str | None = None,
             plugin_id: str | None = None, status: str | None = None,
             limit: int | None = None) -> list[dict[str, Any]]:
        return self._call("GET", "/kernel/runs", query={
            "project_id": project_id, "design_id": design_id,
            "plugin_id": plugin_id, "status": status, "limit": limit,
        })["runs"]

    def run(self, run_id: str) -> dict[str, Any]:
        return self._call("GET", f"/kernel/runs/{_seg(run_id)}")["run"]

    def cancel(self, run_id: str) -> dict[str, Any]:
        return self._call("POST", f"/kernel/runs/{_seg(run_id)}/cancel")

    def retry(self, run_id: str, *, reason: str) -> dict[str, Any]:
        return self._call("POST", f"/kernel/runs/{_seg(run_id)}/retry",
                          payload={"reason": reason})

    def metrics(self, run_id: str, *, complete_only: bool = False
                ) -> list[dict[str, Any]]:
        return self._call("GET", f"/kernel/runs/{_seg(run_id)}/metrics",
                          query={"complete_only": "1" if complete_only else None}
                          )["metrics"]

    def artifacts(self, run_id: str, *, category: str | None = None,
                  format: str | None = None, stage: str | None = None) -> list[dict[str, Any]]:
        return self._call("GET", f"/kernel/runs/{_seg(run_id)}/artifacts",
                          query={"category": category, "format": format, "stage": stage})["artifacts"]

    def timeline(self, run_id: str) -> list[dict[str, Any]]:
        return self._call("GET", f"/kernel/runs/{_seg(run_id)}/timeline"
                          )["timeline"]

    def resources(self, run_id: str) -> dict[str, Any]:
        """Return the platform capacity, reservations, and attempt usage view."""
        return self._call("GET", f"/kernel/runs/{_seg(run_id)}/resources")["resources"]

    def logs(self, run_id: str, *, offset: int = 0,
             max_bytes: int | None = None) -> dict[str, Any]:
        return self._call("GET", f"/kernel/runs/{_seg(run_id)}/logs",
                          query={"offset": offset, "max_bytes": max_bytes})["logs"]

    def artifact_excerpt(self, run_id: str, artifact_id: str, *,
                         offset: int = 0, max_bytes: int = 8192
                         ) -> dict[str, Any]:
        if not 0 < max_bytes <= MAX_EXCERPT_BYTES:
            # Fail here rather than making the caller discover the ceiling from
            # a 400 after a round trip.
            raise ValueError(
                f"max_bytes must be between 1 and {MAX_EXCERPT_BYTES}"
            )
        return self._call(
            "GET",
            f"/kernel/runs/{_seg(run_id)}/artifacts/{_seg(artifact_id)}/excerpt",
            query={"offset": offset, "max_bytes": max_bytes},
        )

    def graph(self, run_ids: Sequence[str]) -> dict[str, Any]:
        return self._call("GET", "/kernel/graph",
                          query={"run_id": list(run_ids)})["graph"]


def _seg(value: str) -> str:
    """Percent-encode one path segment.

    Ids are platform-generated and normally safe, but a client that builds paths
    by concatenation should not be the place where a traversal becomes possible.
    """
    return urllib.parse.quote(str(value), safe="")


def _error_message(exc: urllib.error.HTTPError) -> str:
    body = _safe_json(exc.read())
    if isinstance(body, Mapping) and body.get("error"):
        return str(body["error"])
    return f"kernel returned HTTP {exc.code}"


def _safe_json(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")[:500]
