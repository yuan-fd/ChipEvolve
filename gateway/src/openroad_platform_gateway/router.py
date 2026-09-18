"""One HTTP router for the kernel.

Both the entry point's own routes and the kernel API's routes are registered on
the same router, so the whole platform has exactly one request dispatcher.  The
previous platform had three, in three different styles, and the one that grew to
six thousand lines was the one nobody had factored out.

A route is a method plus a path template.  Templates use ``{name}`` for a single
segment; a captured segment is percent-decoded and may not contain a separator,
so a caller cannot walk out of the path they were given.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

#: A request body larger than this is refused rather than buffered.  The kernel
#: accepts small documents; an unbounded read is a way to exhaust it.
MAX_BODY_BYTES = 1 * 1024 * 1024

#: A response larger than this is refused.  Nothing the kernel serves is large:
#: artifact content is available only in bounded excerpts.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024

#: A single path segment.  It cannot contain a separator, so a captured value
#: cannot walk out of the route it matched.
_SEGMENT = r"(?P<{name}>[^/]+)"

#: A trailing remainder.  Used only where the kernel proxies an app, which
#: owns everything below its own prefix -- and where the app, not the kernel,
#: decides what its sub-paths mean.
_TAIL = r"(?P<{name}>.*)"


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    params: dict[str, str]
    query: dict[str, list[str]]
    headers: Mapping[str, str]
    body: Any = None

    def header(self, name: str, default: str | None = None) -> str | None:
        wanted = name.lower()
        for key, value in self.headers.items():
            if key.lower() == wanted:
                return value
        return default

    def q(self, name: str, default: str | None = None) -> str | None:
        values = self.query.get(name)
        return values[0] if values else default

    def q_all(self, name: str) -> list[str]:
        return list(self.query.get(name, ()))

    def q_int(self, name: str, default: int | None = None) -> int | None:
        raw = self.q(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError as exc:
            raise HttpError(400, f"{name} must be an integer") from exc

    def q_bool(self, name: str) -> bool:
        return (self.q(name) or "").lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Response:
    status: int = 200
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def json(cls, body: Any, status: int = 200) -> Response:
        return cls(status=status, body=body)

    @classmethod
    def error(cls, status: int, message: str) -> Response:
        return cls(status=status, body={"error": message})


class HttpError(Exception):
    """A handler refused the request, with a status the caller can act on."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class _Route:
    method: str
    regex: re.Pattern[str]
    handler: Callable[[Request], Response]


class Router:
    def __init__(self) -> None:
        self._routes: list[_Route] = []

    def add(self, method: str, template: str,
            handler: Callable[[Request], Response]) -> None:
        def expand(match: re.Match[str]) -> str:
            name, tail = match.group(1), match.group(2)
            return (_TAIL if tail else _SEGMENT).format(name=name)

        pattern = "^" + re.sub(r"\{(\w+)(\.\.\.)?\}", expand, template) + "$"
        self._routes.append(_Route(method.upper(), re.compile(pattern), handler))

    def get(self, template: str, handler: Callable[[Request], Response]) -> None:
        self.add("GET", template, handler)

    def post(self, template: str, handler: Callable[[Request], Response]) -> None:
        self.add("POST", template, handler)

    def match(self, method: str, path: str) -> tuple[_Route, dict[str, str]] | None:
        # Longest pattern first, so a tail route does not shadow a more
        # specific one that happens to be registered later.
        for route in sorted(self._routes,
                            key=lambda r: len(r.regex.pattern), reverse=True):
            if route.method != method.upper():
                continue
            found = route.regex.match(path)
            if found:
                params = {
                    key: urllib.parse.unquote(value)
                    for key, value in found.groupdict().items()
                }
                return route, params
        return None

    def allowed_methods(self, path: str) -> set[str]:
        return {
            route.method for route in self._routes if route.regex.match(path)
        }

    def routes(self) -> Iterable[tuple[str, str]]:
        return ((r.method, r.regex.pattern) for r in self._routes)


def make_handler(router: Router):
    """Build the one request handler for the platform."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "openroad-platform/0.2"
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

        def _handle(self, method: str) -> None:
            parsed = urllib.parse.urlparse(self.path)
            try:
                body = self._read_body()
                matched = router.match(method, parsed.path)
                if matched is None:
                    allowed = router.allowed_methods(parsed.path)
                    if allowed:
                        self._send(Response.error(
                            405, f"{method} is not allowed here"
                        ))
                    else:
                        self._send(Response.error(404, "not found"))
                    return
                route, params = matched
                request = Request(
                    method=method, path=parsed.path, params=params,
                    query=urllib.parse.parse_qs(parsed.query),
                    headers=dict(self.headers.items()), body=body,
                )
                response = route.handler(request)
                if not isinstance(response, Response):  # pragma: no cover
                    raise HttpError(500, "handler returned a non-Response")
                self._send(response)
            except HttpError as exc:
                self._send(Response.error(exc.status, exc.message))
            except Exception as exc:  # noqa: BLE001 - turned into a 500 on purpose
                # An unhandled failure is reported as a server error with its
                # type.  It is never dressed up as a 200, because a client that
                # cannot distinguish "failed" from "succeeded" will store the
                # wrong thing.
                self._send(Response.error(
                    500, f"{type(exc).__name__}: {exc}"
                ))

        def _read_body(self) -> Any:
            length = self.headers.get("Content-Length")
            if not length:
                return None
            try:
                size = int(length)
            except ValueError as exc:
                raise HttpError(400, "Content-Length must be an integer") from exc
            if size < 0 or size > MAX_BODY_BYTES:
                raise HttpError(413, f"request body exceeds {MAX_BODY_BYTES} bytes")
            if size == 0:
                return None
            raw = self.rfile.read(size)
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise HttpError(400, "request body must be JSON") from exc

        def _send(self, response: Response) -> None:
            if response.body is None:
                payload = b""
            elif isinstance(response.body, bytes):
                payload = response.body
            else:
                payload = json.dumps(response.body).encode("utf-8")
            if len(payload) > MAX_RESPONSE_BYTES:
                payload = json.dumps({"error": "response too large"}).encode("utf-8")
                response = Response(status=500, body=None)
            self.send_response(response.status)
            self.send_header(
                "Content-Type",
                response.headers.get("Content-Type", "application/json"),
            )
            self.send_header("Content-Length", str(len(payload)))
            for key, value in response.headers.items():
                if key.lower() != "content-type":
                    self.send_header(key, value)
            self.end_headers()
            if payload:
                self.wfile.write(payload)

        def log_message(self, *args: Any) -> None:
            pass

    return Handler


def serve(router: Router, *, host: str, port: int) -> None:
    ThreadingHTTPServer((host, port), make_handler(router)).serve_forever()
