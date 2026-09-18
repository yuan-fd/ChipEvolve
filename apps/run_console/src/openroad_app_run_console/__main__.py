"""Run Console: submit a design, then follow what actually happened.

The console owns nothing.  It submits tasks through the client, reads back the
kernel's record, and stores none of it -- so it can show a caller exactly what
that caller is already allowed to see, and nothing more.

Its one interesting behaviour is that **it does not invent progress**.  A
progress view invites three specific inventions, and this one refuses all three:

* a percentage, which requires progress to be countable before it is known;
* a bar driven by elapsed time against an expected duration, which turns a guess
  into a picture;
* a stage list known in advance, which would mean memorising one tool's stage
  names -- the exact thing that made the previous platform unable to host a
  second tool without being edited.

So the console reports the stage events the kernel actually holds, in the order
they arrived, with stage names carried through as opaque data.  A run with no
events is reported as having **reported nothing**, not as "0% complete": those
are different facts and only one of them is known.  A malformed progress envelope
is surfaced as its own count rather than smoothed away, because a plugin that
cannot be read should not look healthy.

It imports only the client and the standard library (G4), opens no database at
all (G5), and runs as its own process with its own smoke (G6, G9).
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
from collections.abc import Iterable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from openroad_platform_client import KernelClient, KernelError, KernelUnavailable

SERVICE_NAME = "run_console"
DEFAULT_PORT = 8830

#: The kernel's event vocabulary.  The stage name inside a payload belongs to
#: whichever plugin produced it; this app never interprets it.
STAGE_STARTED = "stage.started"
STAGE_FINISHED = "stage.finished"
PROGRESS_MALFORMED = "progress.malformed"

PROGRESS_NOTE = (
    "Only stages the plugin reported are listed. A stage absent from this list "
    "is unreported -- not complete, not skipped, and not zero: the kernel stores "
    "what the tool printed and nothing else."
)


class ConsoleError(Exception):
    """The console refused the request, with a status the caller can act on."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def progress(timeline: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The stages a run reported, and nothing it did not.

    Ordered by first appearance, which is the order the plugin announced them --
    the console has no other ordering to offer and does not impose one.
    """
    stages: dict[str, dict[str, Any]] = {}
    malformed = 0
    for event in timeline:
        payload = event.get("payload") or {}
        kind = event.get("event_type")
        if kind == PROGRESS_MALFORMED:
            malformed += int(payload.get("count") or 0)
            continue
        name = payload.get("stage")
        if not name:
            continue
        entry = stages.setdefault(name, {
            "stage": name, "status": "started", "started_at": None,
            "finished_at": None, "seconds": None, "detail": None,
        })
        if kind == STAGE_STARTED:
            entry["started_at"] = event.get("occurred_at")
            entry["status"] = "running"
        elif kind == STAGE_FINISHED:
            entry["finished_at"] = event.get("occurred_at")
            entry["status"] = payload.get("status") or "finished"
            entry["seconds"] = payload.get("seconds")
            entry["detail"] = payload.get("detail")
    ordered = list(stages.values())
    return {
        "stages": ordered,
        "reported": len(ordered),
        # Said explicitly, so a caller cannot read an empty list as "nothing
        # happened yet" when the truth may be "nothing was recorded".
        "reported_nothing": not ordered,
        "malformed_reports": malformed,
        "note": PROGRESS_NOTE,
    }


def overview(client: KernelClient, *, limit: int = 20) -> dict[str, Any]:
    runs = client.runs(limit=limit)
    return {
        "runs": runs,
        "count": len(runs),
        "note": (
            "This console submits through the kernel and stores nothing of its "
            "own. What it shows is the kernel's record, not a copy of it."
        ),
    }


def run_view(client: KernelClient, run_id: str) -> dict[str, Any]:
    """One run: what happened, what it left, and what is missing."""
    detail = client.run(run_id)
    timeline = client.timeline(run_id)
    artifacts = client.artifacts(run_id)
    metrics = client.metrics(run_id)
    unsourced = [metric for metric in metrics if not metric["complete"]]
    return {
        "run": detail,
        "progress": progress(timeline),
        "artifacts": artifacts,
        "evidence": {
            "artifacts": len(artifacts),
            "kinds": sorted({artifact["kind"] for artifact in artifacts}),
            "metrics": len(metrics),
            "unsourced": len(unsourced),
        },
        "timeline": timeline,
    }


def submission(client: KernelClient, payload: Any) -> dict[str, Any]:
    """Submit a task, or say why not without inventing one.

    The console adds nothing to the task.  A missing field is the kernel's to
    refuse, with its own message, because a console that helpfully filled in a
    default would be writing a request the caller did not make.
    """
    if not isinstance(payload, Mapping) or not isinstance(payload.get("task"), Mapping):
        raise ConsoleError(400, 'a task object is required: {"task": {...}}')
    # The kernel's own envelope is passed through rather than re-wrapped: a
    # console that renamed the field would make callers translate between two
    # names for the same thing.
    created = client.submit(dict(payload["task"]),
                            idempotent=bool(payload.get("idempotent")))
    return {"run": created["run"], "submitted": True}


class Handler(BaseHTTPRequestHandler):
    server_version = f"openroad-app-{SERVICE_NAME}/0.1"

    #: Set by ``serve``.  The console owns no session: it borrows the caller's
    #: token, so it can never see more than the person using it.
    client: KernelClient

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            payload = self._route(parsed.path, urllib.parse.parse_qs(parsed.query))
        except ConsoleError as exc:
            self._send(exc.status, {"error": exc.message})
            return
        except KernelUnavailable as exc:
            self._send(503, {"error": str(exc)})
            return
        except KernelError as exc:
            self._send(exc.status or 502, {"error": str(exc)})
            return
        self._send(200, payload)

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        try:
            payload = self._body()
            client = self._client()
            if path == "/runs":
                self._send(201, submission(client, payload))
                return
            if path.startswith("/runs/") and path.endswith("/cancel"):
                run_id = urllib.parse.unquote(
                    path[len("/runs/"):-len("/cancel")]).strip("/")
                if not run_id:
                    raise ConsoleError(400, "a run id is required")
                self._send(200, client.cancel(run_id))
                return
        except ConsoleError as exc:
            self._send(exc.status, {"error": exc.message})
            return
        except KernelUnavailable as exc:
            self._send(503, {"error": str(exc)})
            return
        except KernelError as exc:
            self._send(exc.status or 502, {"error": str(exc)})
            return
        self._send(404, {"error": "not found"})

    def _route(self, path: str, query: dict[str, list[str]]) -> dict[str, Any]:
        if path == "/health":
            return {"app": SERVICE_NAME, "status": "ok",
                    "kernel": self.client.health()}
        if path in ("/", "/runs"):
            return overview(self._client(), limit=_int_param(query, "limit", 20))
        if path == "/plugins":
            # Its own route rather than part of the landing page: a caller
            # without permission to list plugins should still be able to read
            # runs, and a 403 from one call must not blank the whole console.
            return {"plugins": self._client().plugins()}
        if path.startswith("/runs/"):
            run_id = urllib.parse.unquote(path[len("/runs/"):])
            if not run_id:
                raise ConsoleError(400, "a run id is required")
            return run_view(self._client(), run_id)
        raise ConsoleError(404, "not found")

    def _body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            raise ConsoleError(400, f"the request body is not JSON: {exc}") from exc

    def _client(self) -> KernelClient:
        """Forward the caller's token rather than inventing one."""
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
    parser = argparse.ArgumentParser(description="Run Console")
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
