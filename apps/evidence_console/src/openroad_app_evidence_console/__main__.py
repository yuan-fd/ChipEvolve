"""Evidence Console: read runs and their provenance.

A small, honest application.  It owns no evidence, stores nothing, and can do
nothing the kernel cannot already do -- it presents what the kernel holds,
through the client, and never touches the kernel's database.

It exists to demonstrate the application contract:

* it imports only ``openroad_platform_client`` and the standard library (G4);
* it opens no database at all, let alone the kernel's (G5);
* it runs as its own process on its own port, with its own smoke (G6, G9).

The interesting behaviour is in how it reports absence.  A run with a metric
that cites no artifact is shown as **unsourced**, not hidden and not silently
totalled: the kernel records it, so the console says so.
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from openroad_platform_client import KernelClient, KernelError, KernelUnavailable

SERVICE_NAME = "evidence_console"
DEFAULT_PORT = 8810


class ConsoleError(Exception):
    """The console refused the request, with a status the caller can act on."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def overview(client: KernelClient, *, limit: int = 20) -> dict[str, Any]:
    """What the console shows first: recent runs."""
    runs = client.runs(limit=limit)
    return {
        "runs": runs,
        "count": len(runs),
        "note": (
            "Runs are the kernel's record. This console reads them through the "
            "client and stores nothing of its own."
        ),
    }


def run_view(client: KernelClient, run_id: str) -> dict[str, Any]:
    """One run with its evidence, and an explicit completeness statement.

    ``unsourced`` is the number of metrics that cite no artifact.  Reporting it
    beside the metrics is the point: a number with no source is not evidence,
    and a reader should not have to check each one to find out.
    """
    detail = client.run(run_id)
    metrics = client.metrics(run_id)
    unsourced = [m for m in metrics if not m["complete"]]
    return {
        "run": detail,
        "metrics": metrics,
        "timeline": client.timeline(run_id),
        "evidence": {
            "metrics": len(metrics),
            "sourced": len(metrics) - len(unsourced),
            "unsourced": len(unsourced),
            "complete": not unsourced,
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = f"openroad-app-{SERVICE_NAME}/0.1"

    #: Set by ``serve``.  The console does not own a session; it borrows the
    #: caller's token and passes it straight through, so the console can never
    #: see more than the person using it.
    client: KernelClient

    def do_GET(self) -> None:  # noqa: N802 - required by the base class
        parsed = urllib.parse.urlparse(self.path)
        try:
            payload = self._route(parsed.path, urllib.parse.parse_qs(parsed.query))
        except ConsoleError as exc:
            self._send(exc.status, {"error": exc.message})
            return
        except KernelUnavailable as exc:
            # The kernel being down is 503: this console is fine, the thing it
            # reads is not.
            self._send(503, {"error": str(exc)})
            return
        except KernelError as exc:
            self._send(exc.status or 502, {"error": str(exc)})
            return
        self._send(200, payload)

    def _route(self, path: str, query: dict[str, list[str]]) -> dict[str, Any]:
        if path == "/health":
            return {"app": SERVICE_NAME, "status": "ok",
                    "kernel": self.client.health()}
        if path in ("/", "/runs"):
            return overview(self._client(), limit=_int_param(query, "limit", 20))
        if path.startswith("/runs/"):
            run_id = urllib.parse.unquote(path[len("/runs/"):])
            if not run_id:
                raise ConsoleError(400, "a run id is required")
            return run_view(self._client(), run_id)
        raise ConsoleError(404, "not found")

    def _client(self) -> KernelClient:
        """Forward the caller's token rather than inventing one.

        The console holds no credentials, so it cannot show a caller more than
        that caller is already allowed to see.
        """
        header = self.headers.get("Authorization", "") or ""
        self.client.token = (header[7:].strip()
                             if header.lower().startswith("bearer ") else None)
        return self.client

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


def _int_param(query: dict[str, list[str]], name: str, default: int) -> int:
    values = query.get(name)
    if not values:
        return default
    try:
        return int(values[0])
    except ValueError as exc:
        raise ConsoleError(400, f"{name} must be an integer") from exc


def serve(*, host: str, port: int, kernel_url: str) -> None:
    handler = type("BoundHandler", (Handler,), {"client": KernelClient(kernel_url)})
    ThreadingHTTPServer((host, port), handler).serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evidence Console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--kernel-url", default="http://127.0.0.1:8700",
                        help="the platform entry point that serves the kernel")
    args = parser.parse_args(argv)
    serve(host=args.host, port=args.port, kernel_url=args.kernel_url)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
