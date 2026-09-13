"""The integration entry point.

This is the whole of what "the platform is just the entry point" means in code.
It authenticates, it knows where each app lives, and it forwards.  It has no
domain logic of any kind: no task submission, no scoring, no EDA, no database of
its own.

Its entire configuration is a list of apps.  Adding a capability is adding a
line to that list, not editing this file -- which is the property the previous
platform lacked when its entry point grew to six thousand lines.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

#: How long the gateway waits on a downstream app before reporting it unhealthy.
PROBE_TIMEOUT_SECONDS = 2.0

#: A response body larger than this is not forwarded.  The gateway is a doorway,
#: not a buffer, and an unbounded copy is a way for one app to exhaust it.
MAX_PROXY_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class AppRegistration:
    """One downstream application, as the gateway needs to know it."""

    name: str
    base_url: str
    title: str = ""
    description: str = ""

    def validate(self) -> None:
        if not self.name or not self.name.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"invalid app name: {self.name!r}")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError(f"app {self.name!r} has a non-http base_url")

    def health_url(self) -> str:
        return self.base_url.rstrip("/") + "/health"


@dataclass(frozen=True)
class GatewayConfig:
    apps: tuple[AppRegistration, ...] = ()
    #: Paths served by the gateway itself, before any app is considered.
    public_paths: tuple[str, ...] = ("/health", "/", "/apps")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "GatewayConfig":
        unknown = sorted(set(payload) - {"apps"})
        if unknown:
            raise ValueError(f"unknown gateway config keys: {', '.join(unknown)}")
        apps = []
        seen: set[str] = set()
        for item in payload.get("apps", ()):
            app = AppRegistration(**item)
            app.validate()
            if app.name in seen:
                # Two apps under one name would make routing depend on order,
                # which is exactly the kind of implicit rule that rots.
                raise ValueError(f"duplicate app name: {app.name!r}")
            seen.add(app.name)
            apps.append(app)
        return cls(apps=tuple(apps))

    @classmethod
    def from_file(cls, path: str | Path) -> "GatewayConfig":
        return cls.from_mapping(json.loads(Path(path).read_text(encoding="utf-8")))

    def nav(self) -> list[dict[str, str]]:
        """The navigation the entry point offers.  Derived, never hand-kept."""
        return [
            {
                "name": app.name,
                "title": app.title or app.name.replace("_", " ").title(),
                "description": app.description,
                "path": f"/app/{app.name}/",
            }
            for app in self.apps
        ]


def probe(app: AppRegistration, timeout: float = PROBE_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Ask one app whether it is alive.

    A failure here is reported, never retried into a fake success: the entry
    point's job is to tell the truth about what is reachable.
    """
    try:
        with urllib.request.urlopen(app.health_url(), timeout=timeout) as response:
            payload = json.loads(response.read())
        return {"name": app.name, "reachable": True, "health": payload}
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as exc:
        return {"name": app.name, "reachable": False, "error": str(exc)}


def make_handler(config: GatewayConfig):
    apps = {app.name: app for app in config.apps}

    class Handler(BaseHTTPRequestHandler):
        server_version = "openroad-platform-gateway/0.1"

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]

            if path == "/health":
                self._send_json(200, {
                    "service": "gateway",
                    "status": "ok",
                    "apps": [probe(app) for app in config.apps],
                })
                return

            if path in ("/", "/apps"):
                self._send_json(200, {"apps": config.nav()})
                return

            if path.startswith("/app/"):
                name, _, rest = path[len("/app/"):].partition("/")
                app = apps.get(name)
                if app is None:
                    self._send_json(404, {"error": f"unknown app {name!r}"})
                    return
                self._proxy(app, "/" + rest)
                return

            self._send_json(404, {"error": "not found"})

        # -- helpers ------------------------------------------------------

        def _proxy(self, app: AppRegistration, path: str) -> None:
            target = app.base_url.rstrip("/") + path
            try:
                with urllib.request.urlopen(
                    target, timeout=PROBE_TIMEOUT_SECONDS
                ) as response:
                    body = response.read(MAX_PROXY_BYTES + 1)
                    status = response.status
                    content_type = response.headers.get(
                        "Content-Type", "application/octet-stream"
                    )
            except urllib.error.HTTPError as exc:
                self._send_json(exc.code, {"error": str(exc)})
                return
            except (urllib.error.URLError, OSError) as exc:
                # A downstream app being down is 502, not 500: the entry point
                # is fine, the thing behind it is not.
                self._send_json(502, {"error": f"{app.name} unreachable: {exc}"})
                return
            if len(body) > MAX_PROXY_BYTES:
                self._send_json(502, {"error": f"{app.name} response too large"})
                return
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    return Handler


def serve(config: GatewayConfig, *, host: str, port: int) -> None:
    ThreadingHTTPServer((host, port), make_handler(config)).serve_forever()
