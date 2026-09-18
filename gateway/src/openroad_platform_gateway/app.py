"""The integration entry point.

This is what "the platform is just the entry point" means in code.  It serves
the kernel's own surface, and it routes everything else to the application that
owns it.  It holds no domain logic, opens no database of its own, and knows no
capability by name.

It is also the component that grew to six thousand lines in the previous
platform, so its thinness is asserted rather than intended: `test_gateway.py`
fails if this package opens a database or imports a kernel-internals package.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .router import HttpError, Request, Response, Router, make_handler, serve

#: How long the entry point waits on a downstream application.
PROBE_TIMEOUT_SECONDS = 2.0

#: A proxied response larger than this is refused.  The entry point is a
#: doorway, not a buffer.
MAX_PROXY_BYTES = 8 * 1024 * 1024

#: Prefix under which an application is mounted.
APP_PREFIX = "/app"

#: Prefix under which the kernel serves its own surface.
KERNEL_PREFIX = "/kernel"


@dataclass(frozen=True)
class AppRegistration:
    """One downstream application, as the entry point needs to know it."""

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

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> GatewayConfig:
        unknown = sorted(set(payload) - {"apps"})
        if unknown:
            raise ValueError(f"unknown gateway config keys: {', '.join(unknown)}")
        apps: list[AppRegistration] = []
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
    def from_file(cls, path: str | Path) -> GatewayConfig:
        return cls.from_mapping(json.loads(Path(path).read_text(encoding="utf-8")))

    def nav(self) -> list[dict[str, str]]:
        """The navigation the entry point offers.  Derived, never hand-kept."""
        return [
            {
                "name": app.name,
                "title": app.title or app.name.replace("_", " ").title(),
                "description": app.description,
                "path": f"{APP_PREFIX}/{app.name}/",
            }
            for app in self.apps
        ]


def probe(app: AppRegistration,
          timeout: float = PROBE_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Ask one application whether it is alive.

    A failure is reported, never retried into a fake success: the entry point's
    job is to tell the truth about what is reachable.
    """
    try:
        with urllib.request.urlopen(app.health_url(), timeout=timeout) as response:
            payload = json.loads(response.read())
        return {"name": app.name, "reachable": True, "health": payload}
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as exc:
        return {"name": app.name, "reachable": False, "error": str(exc)}


def build_router(config: GatewayConfig, kernel: Any | None = None) -> Router:
    """Assemble the entry point's routes.

    ``kernel`` is a KernelApi or None.  Registering it here keeps both surfaces
    on one dispatcher, which is why the platform has exactly one.
    """
    apps = {app.name: app for app in config.apps}
    router = Router()

    if kernel is not None:
        kernel.register(router)

    def health(request: Request) -> Response:
        return Response.json({
            "service": "gateway",
            "status": "ok",
            "kernel": "attached" if kernel is not None else "absent",
            "apps": [probe(app) for app in config.apps],
        })

    def navigation(request: Request) -> Response:
        return Response.json({"apps": config.nav()})

    def proxy(request: Request) -> Response:
        name = request.params["name"]
        app = apps.get(name)
        if app is None:
            raise HttpError(404, f"unknown app {name!r}")
        tail = request.params.get("rest") or ""
        path = "/" + tail if tail else "/"
        if request.query:
            path += "?" + urlencode(
                [(key, value) for key, values in request.query.items()
                 for value in values]
            )
        return _forward(app, path, method=request.method, body=request.body)

    router.get("/health", health)
    router.get("/", navigation)
    router.get("/apps", navigation)
    router.get(f"{APP_PREFIX}/{{name}}", proxy)
    # A tail route owns everything below the app's prefix; that is the app's
    # namespace, not the kernel's.
    router.get(f"{APP_PREFIX}/{{name}}/{{rest...}}", proxy)
    router.post(f"{APP_PREFIX}/{{name}}/{{rest...}}", proxy)
    return router


def _forward(app: AppRegistration, path: str, *, method: str,
             body: Any = None) -> Response:
    url = app.base_url.rstrip("/") + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request,
                                    timeout=PROBE_TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_PROXY_BYTES + 1)
            content_type = response.headers.get("Content-Type", "application/json")
            status = response.status
    except urllib.error.HTTPError as exc:
        raise HttpError(exc.code, _detail(exc)) from exc
    except (urllib.error.URLError, OSError) as exc:
        # A downstream app being down is 502, not 500: the entry point is fine,
        # the thing behind it is not.
        raise HttpError(502, f"{app.name} is unreachable: {exc}") from exc
    if len(raw) > MAX_PROXY_BYTES:
        raise HttpError(502, f"{app.name} returned an oversized response")
    try:
        return Response.json(json.loads(raw), status=status)
    except json.JSONDecodeError:
        return Response(status=status, body=raw,
                        headers={"Content-Type": content_type})


def _detail(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read())
        if isinstance(payload, Mapping) and payload.get("error"):
            return str(payload["error"])
    except (ValueError, OSError):
        return f"upstream returned HTTP {exc.code}"
    return f"upstream returned HTTP {exc.code}"


__all__ = (
    "APP_PREFIX",
    "KERNEL_PREFIX",
    "MAX_PROXY_BYTES",
    "PROBE_TIMEOUT_SECONDS",
    "AppRegistration",
    "GatewayConfig",
    "build_router",
    "make_handler",
    "probe",
    "serve",
)
