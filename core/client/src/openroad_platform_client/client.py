"""Typed HTTP client for the kernel service."""

from __future__ import annotations

import json
import secrets
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
              payload: Mapping[str, Any] | bytes | None = None,
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
            if isinstance(payload, bytes):
                body = payload
                headers["Content-Type"] = "application/octet-stream"
            else:
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

    def upload_input(self, content: bytes) -> dict[str, Any]:
        """Upload bytes that a later TaskSpec can reference by input_id."""
        if not isinstance(content, bytes):
            raise TypeError("input content must be bytes")
        return self._call("POST", "/kernel/inputs", payload=content)["input"]

    def upload_input_chunks(self, chunks: Sequence[bytes]) -> dict[str, Any]:
        """Upload a potentially large input without one oversized request."""
        upload_id = secrets.token_urlsafe(24)
        offset = 0
        result: dict[str, Any] | None = None
        for index, chunk in enumerate(chunks):
            if not isinstance(chunk, bytes):
                raise TypeError("input chunks must be bytes")
            result = self._call(
                "POST", "/kernel/inputs/chunk", payload=chunk,
                query={"upload_id": upload_id, "offset": offset,
                       "final": "1" if index == len(chunks) - 1 else None},
            )
            offset = int(result.get("offset", offset + len(chunk)))
        if result is None or "input" not in result:
            raise ValueError("at least one input chunk is required")
        return result["input"]

    # -- runs -------------------------------------------------------------

    def submit(self, task: Mapping[str, Any], *,
               idempotent: bool = False,
               idempotency_key: str | None = None,
               plugin_version: str | None = None) -> dict[str, Any]:
        """Submit a task, optionally deduplicated by task id or stable key."""
        task_payload = dict(task)
        if plugin_version is not None:
            existing_version = task_payload.get("plugin_version")
            if existing_version is not None and existing_version != plugin_version:
                raise ValueError(
                    "plugin_version argument conflicts with the task payload"
                )
            task_payload["plugin_version"] = plugin_version
        return self._call("POST", "/kernel/runs",
                          payload={"task": task_payload},
                          query={"idempotent": "1" if idempotent else None,
                                 "idempotency_key": idempotency_key})

    def runs(self, *, project_id: str | None = None, design_id: str | None = None,
             plugin_id: str | None = None, status: str | None = None,
             limit: int | None = None, offset: int = 0) -> list[dict[str, Any]]:
        return self._call("GET", "/kernel/runs", query={
            "project_id": project_id, "design_id": design_id,
            "plugin_id": plugin_id, "status": status, "limit": limit,
            "offset": offset,
        })["runs"]

    def run(self, run_id: str) -> dict[str, Any]:
        return self._call("GET", f"/kernel/runs/{_seg(run_id)}")["run"]

    def design_tree(self, design_id: str, *, limit: int | None = None,
                    offset: int = 0) -> dict[str, Any]:
        return self._call("GET", f"/kernel/designs/{_seg(design_id)}/tree",
                          query={"limit": limit, "offset": offset})

    def cancel(self, run_id: str) -> dict[str, Any]:
        return self._call("POST", f"/kernel/runs/{_seg(run_id)}/cancel")

    def retry(self, run_id: str, *, reason: str) -> dict[str, Any]:
        return self._call("POST", f"/kernel/runs/{_seg(run_id)}/retry",
                          payload={"reason": reason})

    def bundle(self, run_id: str) -> dict[str, Any]:
        """Create a local ZIP evidence artifact for a completed or attempted run."""
        return self._call("POST", f"/kernel/runs/{_seg(run_id)}/bundle")["bundle"]

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

    def artifact_bytes(self, run_id: str, artifact_id: str) -> bytes:
        """Download a complete registered artifact within the server limit."""
        return self._call(
            "GET",
            f"/kernel/runs/{_seg(run_id)}/artifacts/{_seg(artifact_id)}/download",
            want_bytes=True,
        )

    def artifact_chunk(self, run_id: str, artifact_id: str, *, offset: int = 0,
                       max_bytes: int = MAX_EXCERPT_BYTES) -> dict[str, Any]:
        if not 0 < max_bytes <= MAX_EXCERPT_BYTES:
            raise ValueError(f"max_bytes must be between 1 and {MAX_EXCERPT_BYTES}")
        return self._call(
            "GET", f"/kernel/runs/{_seg(run_id)}/artifacts/"
            f"{_seg(artifact_id)}/chunk",
            query={"offset": offset, "max_bytes": max_bytes},
        )

    def download_artifact_to(self, run_id: str, artifact_id: str,
                             destination: str, *,
                             chunk_size: int = MAX_EXCERPT_BYTES) -> dict[str, Any]:
        import base64
        offset = 0
        digest = None
        with open(destination, "wb") as output:
            while True:
                chunk = self.artifact_chunk(run_id, artifact_id, offset=offset,
                                            max_bytes=chunk_size)
                data = base64.b64decode(chunk["data_base64"])
                output.write(data)
                offset += len(data)
                digest = chunk["sha256"]
                if not chunk["truncated"]:
                    break
        return {"path": destination, "size_bytes": offset, "sha256": digest}

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
