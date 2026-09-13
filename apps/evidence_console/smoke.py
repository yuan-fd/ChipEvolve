"""Evidence Console - end-to-end smoke.

G9 requires every app to start as a real process and prove it.  This one needs
a kernel to read, so the smoke starts a stub kernel that answers the two calls
the console makes, then starts the console against it and drives both routes
over HTTP.

A stub rather than the real kernel on purpose: this smoke is about the console
being wired correctly, and it should not fail because a plugin is missing or a
toolchain is absent.  The real composition is covered by
``tests/test_kernel_api_end_to_end.py``.
"""

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
#: In a real deployment the client is an installed distribution.  Running
#: from a checkout, its source root has to be on the path.
REPO_ROOT = APP_DIR.parents[1]
CLIENT_SRC = REPO_ROOT / "core" / "client" / "src"
DEFAULT_PORT = 8810


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class StubKernel(BaseHTTPRequestHandler):
    """Just enough kernel: health, a run list, and one run's view."""

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/kernel/health"):
            payload = {"service": "kernel", "status": "ok"}
        elif self.path.startswith("/kernel/runs?"):
            payload = {"runs": [{"run_id": "run-1", "task_id": "t",
                                 "status": "succeeded"}]}
        elif self.path.startswith("/kernel/runs/run-1/metrics"):
            payload = {"metrics": [
                {"name": "area", "value": 1.0, "complete": True},
                {"name": "orphan", "value": 2.0, "complete": False},
            ]}
        elif self.path.startswith("/kernel/runs/run-1/timeline"):
            payload = {"timeline": []}
        elif self.path.startswith("/kernel/runs/run-1"):
            payload = {"run": {"run_id": "run-1", "status": "succeeded",
                               "stages": []}}
        else:
            payload = {"error": "not found"}
            body = json.dumps(payload).encode()
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def wait_for(url: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                return json.loads(response.read())
        except Exception as exc:  # noqa: BLE001 - retried until the deadline
            last = exc
            time.sleep(0.2)
    raise RuntimeError(f"{url} never became healthy: {last}")


def main() -> int:
    kernel_port = free_port()
    app_port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT

    kernel_server = ThreadingHTTPServer(("127.0.0.1", kernel_port), StubKernel)
    threading.Thread(target=kernel_server.serve_forever, daemon=True).start()

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SRC_DIR), str(CLIENT_SRC)]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    app = subprocess.Popen(
        [sys.executable, "-m", "openroad_app_evidence_console",
         "--host", "127.0.0.1", "--port", str(app_port),
         "--kernel-url", f"http://127.0.0.1:{kernel_port}"],
        cwd=str(APP_DIR), env=env,
        stderr=subprocess.PIPE, text=True,
    )
    base = f"http://127.0.0.1:{app_port}"
    try:
        try:
            payload = wait_for(f"{base}/health")
        except RuntimeError:
            # Surface why the child died instead of only that it did.
            if app.poll() is not None and app.stderr is not None:
                raise RuntimeError(
                    "the app exited before becoming healthy:\n"
                    + app.stderr.read()
                ) from None
            raise
        assert payload["app"] == "evidence_console", payload
        assert payload["kernel"]["status"] == "ok", payload

        status, listing = get(f"{base}/runs")
        assert status == 200 and listing["count"] == 1, listing

        status, view = get(f"{base}/runs/run-1")
        assert status == 200, view
        # The console must report that one metric has no source rather than
        # presenting both as evidence.
        assert view["evidence"] == {"metrics": 2, "sourced": 1, "unsourced": 1,
                                    "complete": False}, view["evidence"]

        print("evidence_console smoke: ok (health, list, run view)")
        return 0
    finally:
        app.terminate()
        app.wait(timeout=10)
        kernel_server.shutdown()
        kernel_server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
