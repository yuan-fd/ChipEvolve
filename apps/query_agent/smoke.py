"""Query Agent - end-to-end smoke against a stub kernel."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
SRC_DIR = APP_DIR / "src"
CLIENT_SRC = APP_DIR.parents[1] / "core" / "client" / "src"
DEFAULT_PORT = 8850


class StubKernel(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/kernel/health":
            self._reply({"service": "kernel", "status": "ok"})
        elif path == "/kernel/plugins":
            self._reply({"plugins": [{"plugin_id": "reporter",
                                       "plugin_version": "1",
                                       "capabilities": ["report"],
                                       "executable": True}]})
        elif path == "/kernel/runs":
            self._reply({"runs": [{"run_id": "run-1", "project_id": "p",
                                   "design_id": "gcd", "design_revision_id": "v1",
                                   "status": "succeeded"}]})
        elif path == "/kernel/runs/run-1":
            self._reply({"run": {"run_id": "run-1", "design_id": "gcd",
                                  "design_revision_id": "v1", "status": "succeeded"}})
        elif path.endswith("/artifacts"):
            self._reply({"artifacts": [{"artifact_id": "art-1", "kind": "report",
                                         "store_key": "reports/final.rpt",
                                         "sha256": "0" * 64, "size_bytes": 4}]})
        elif path.endswith("/metrics"):
            self._reply({"metrics": [{"name": "area", "value": 10,
                                       "unit": "um2", "complete": True,
                                       "run_id": "run-1", "attempt_id": "a-1",
                                       "artifact": {"artifact_id": "art-1"},
                                       "parser": {"id": "p", "version": "1"}}]})
        elif path.endswith("/excerpt"):
            self._reply({"artifact_id": "art-1", "sha256": "0" * 64,
                         "offset": 0, "text": "area", "bytes_read": 4,
                         "size_bytes": 4, "truncated": False})
        else:
            self._reply({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        if self.path != "/kernel/runs":
            self._reply({"error": "not found"}, status=404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length))
        task = body["task"]
        self._reply({"run": {"run_id": task["task_id"],
                              "design_id": task["design_id"],
                              "status": "queued"}}, status=201)

    def _reply(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def wait_for_health(port: int, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=1
            ) as response:
                return json.loads(response.read())
        except Exception as exc:  # noqa: BLE001 - retried until the deadline
            last = exc
            time.sleep(0.2)
    raise RuntimeError(f"{APP_DIR.name} did not become healthy: {last}")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    kernel_port = _free_port()
    kernel = ThreadingHTTPServer(("127.0.0.1", kernel_port), StubKernel)
    threading.Thread(target=kernel.serve_forever, daemon=True).start()
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SRC_DIR), str(CLIENT_SRC)]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "openroad_app_query_agent.__main__",
         "--host", "127.0.0.1", "--port", str(port),
         "--kernel-url", f"http://127.0.0.1:{kernel_port}"],
        cwd=str(APP_DIR), env=env,
    )
    try:
        payload = wait_for_health(port)
        assert payload["app"] == "query_agent", payload
        assert payload["status"] == "ok", payload
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/designs/gcd") as response:
            design = json.loads(response.read())
        assert design["runs"][0]["design_revision_id"] == "v1", design
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/query",
            data=json.dumps({"question": "show design gcd"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request) as response:
            report = json.loads(response.read())
        assert report["report"]["runs"][0]["facts"][0]["source"]["complete"]
        preview_request = urllib.request.Request(
            f"http://127.0.0.1:{port}/analysis/preview",
            data=json.dumps({"input_run_id": "run-1", "task": {
                "task_id": "analysis-1", "project_id": "p", "design_id": "gcd",
                "plugin_id": "reporter", "parameters": {"command": "report"},
            }}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(preview_request) as response:
            preview = json.loads(response.read())
        assert preview["approval_required"] is True
        submit_request = urllib.request.Request(
            f"http://127.0.0.1:{port}/analysis/submit",
            data=json.dumps({"confirm": True, "input_run_id": "run-1",
                             "task": preview["task"]}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(submit_request) as response:
            submitted = json.loads(response.read())
        assert submitted["approval"]["confirmed"] is True
        print("query_agent smoke: ok")
        return 0
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        kernel.shutdown()
        kernel.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
