"""HTTP transport for Query-Agent evidence views."""

from __future__ import annotations

import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from openroad_platform_client import KernelClient, KernelError, KernelUnavailable

from .analysis import preview_analysis, submit_analysis
from .evidence import (
    QueryError,
    artifact_view,
    designs,
    integer,
    plan_question,
    run_filters,
    run_report,
    structured_query,
)

SERVICE_NAME = "query_agent"


class Handler(BaseHTTPRequestHandler):
    server_version = "openroad-app-query_agent/0.1"
    client: KernelClient

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path.endswith("/download"):
                run_id, artifact_id = artifact_parts(parsed.path, "/download")
                self._send_bytes(self._borrow().artifact_bytes(run_id, artifact_id))
                return
            payload = self._get(parsed.path, urllib.parse.parse_qs(parsed.query))
        except QueryError as exc:
            self._send(exc.status, {"error": exc.message})
            return
        except KernelUnavailable as exc:
            self._send(503, {"error": str(exc)})
            return
        except KernelError as exc:
            self._send(exc.status or 502, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send(400, {"error": str(exc)})
            return
        self._send(200, payload)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            payload = self._read_json()
            if parsed.path.startswith("/runs/") and parsed.path.endswith("/bundle"):
                run_id = urllib.parse.unquote(
                    parsed.path[len("/runs/"):-len("/bundle")]
                ).strip("/")
                if not run_id:
                    raise QueryError(400, "run_id is required")
                self._send(201, {"bundle": self._borrow().bundle(run_id)})
                return
            if parsed.path == "/query":
                question = payload.get("question")
                if "question" in payload and not isinstance(question, str):
                    raise QueryError(400, "question must be a string")
                result = (plan_question(self._borrow(), question)
                          if isinstance(question, str)
                          else structured_query(self._borrow(), payload))
                self._send(200, result)
                return
            if parsed.path == "/analysis/preview":
                self._send(200, preview_analysis(self._borrow(), payload))
                return
            if parsed.path == "/analysis/submit":
                self._send(202, submit_analysis(self._borrow(), payload))
                return
            raise QueryError(404, "not found")
        except QueryError as exc:
            self._send(exc.status, {"error": exc.message})
        except KernelUnavailable as exc:
            self._send(503, {"error": str(exc)})
        except KernelError as exc:
            self._send(exc.status or 502, {"error": str(exc)})
        except ValueError as exc:
            self._send(400, {"error": str(exc)})

    def _get(self, path: str, query: dict[str, list[str]]) -> dict[str, Any]:
        client = self._borrow()
        if path == "/health":
            return {"app": SERVICE_NAME, "status": "ok", "kernel": client.health()}
        if path == "/analysis/capabilities":
            return {"capabilities": client.plugins()}
        if path == "/designs":
            runs = client.runs(**run_filters(query))
            return {"designs": designs(runs), "count": len(runs)}
        if path.startswith("/designs/") and path.endswith("/tree"):
            design_id = urllib.parse.unquote(path[len("/designs/"):-len("/tree")]).strip("/")
            if not design_id:
                raise QueryError(400, "design_id is required")
            return client.design_tree(design_id)
        if path.startswith("/designs/"):
            design_id = urllib.parse.unquote(path[len("/designs/"):])
            if not design_id:
                raise QueryError(400, "design_id is required")
            runs = client.runs(design_id=design_id, limit=100)
            return {"design_id": design_id, "runs": runs, "count": len(runs)}
        if path.startswith("/runs/") and path.endswith("/artifacts"):
            run_id = urllib.parse.unquote(path[len("/runs/"):-len("/artifacts")])
            artifacts = client.artifacts(
                run_id,
                category=query.get("category", [None])[0],
                format=query.get("format", [None])[0],
                stage=query.get("stage", [None])[0],
            )
            for name in ("path", "sha256", "kind"):
                value = query.get(name, [None])[0]
                if value:
                    key = "store_key" if name == "path" else name
                    artifacts = [a for a in artifacts if a.get(key) == value]
            return {"run_id": run_id,
                    "artifacts": [artifact_view(run_id, artifact)
                                  for artifact in artifacts],
                    "filters": {k: v[0] for k, v in query.items() if v}}
        if path.startswith("/runs/") and path.endswith("/metrics"):
            run_id = urllib.parse.unquote(path[len("/runs/"):-len("/metrics")])
            return {"run_id": run_id, "metrics": client.metrics(run_id)}
        if path.startswith("/runs/"):
            run_id = urllib.parse.unquote(path[len("/runs/"):])
            if not run_id:
                raise QueryError(400, "run_id is required")
            return {"report": run_report(client, [run_id])}
        if "/artifacts/" in path and path.endswith("/excerpt"):
            run_id, artifact_id = artifact_parts(path, "/excerpt")
            offset = integer(query.get("offset", [None])[0], "offset", 0)
            max_bytes = integer(query.get("max_bytes", [None])[0], "max_bytes", 8192)
            return client.artifact_excerpt(run_id, artifact_id, offset=offset,
                                           max_bytes=max_bytes)
        raise QueryError(404, "not found")

    def _borrow(self) -> KernelClient:
        header = self.headers.get("Authorization", "") or ""
        token = (header[7:].strip()
                 if header.lower().startswith("bearer ") else None)
        # The handler class is shared by ThreadingHTTPServer.  A request must
        # never mutate a client carrying another user's token.
        return KernelClient(self.client.base_url, token=token,
                            timeout=self.client.timeout)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1024 * 1024:
            raise QueryError(400, "request body must be a non-empty JSON object")
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise QueryError(400, "request body must be JSON") from exc
        if not isinstance(payload, dict):
            raise QueryError(400, "request body must be a JSON object")
        return payload

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


def artifact_parts(path: str, suffix: str) -> tuple[str, str]:
    raw = path[len("/artifacts/"):-len(suffix)]
    parts = raw.split("/", 1)
    if len(parts) != 2 or not all(parts):
        raise QueryError(400, "run_id and artifact_id are required")
    return urllib.parse.unquote(parts[0]), urllib.parse.unquote(parts[1])


def serve(*, host: str, port: int, kernel_url: str) -> None:
    handler = type("BoundHandler", (Handler,), {"client": KernelClient(kernel_url)})
    ThreadingHTTPServer((host, port), handler).serve_forever()
