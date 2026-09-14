"""DSE Lab - end-to-end smoke.

G9 requires every app to prove it starts as a real process.  This one needs a
kernel, so the smoke starts a stub that answers the calls the lab makes, then
starts the lab against it and drives a whole sweep over HTTP: create, list,
detail, compare.

A stub rather than the real kernel on purpose: this smoke is about the lab being
wired correctly and should not fail because a plugin is missing.  The real
composition, with a live worker executing the submitted points, is covered by
tests/test_dse_lab.py.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
SRC_DIR = APP_DIR / "src"
#: In a real deployment the client is an installed distribution.  Running from a
#: checkout, its source root has to be on the path.
REPO_ROOT = APP_DIR.parents[1]
CLIENT_SRC = REPO_ROOT / "core" / "client" / "src"
DEFAULT_PORT = 8820

#: What the stub kernel remembers about a submitted task.
SUBMITTED: dict[str, dict] = {}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class StubKernel(BaseHTTPRequestHandler):
    """Just enough kernel: health, submit, read one run, and its metrics."""

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/kernel/health"):
            return self._reply(200, {"service": "kernel", "status": "ok"})
        if self.path.startswith("/kernel/runs/") and self.path.endswith("/metrics"):
            run_id = self.path[len("/kernel/runs/"):-len("/metrics")]
            task = SUBMITTED.get(run_id, {})
            util = float((task.get("parameters") or {}).get("core_utilization_pct", 0))
            return self._reply(200, {"metrics": [
                {"name": "core_utilization_pct", "value": util, "complete": True,
                 "artifact": {"artifact_id": "art-1", "sha256": "0" * 64}},
            ]})
        if self.path.startswith("/kernel/runs/"):
            run_id = self.path[len("/kernel/runs/"):]
            if run_id not in SUBMITTED:
                return self._reply(404, {"error": "unknown run"})
            return self._reply(200, {"run": {
                "run_id": run_id, "status": "succeeded", "stages": [],
                "terminal_reason": None,
            }})
        return self._reply(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else {}
        if self.path.startswith("/kernel/runs"):
            task = body["task"]
            run_id = f"run-{len(SUBMITTED) + 1}"
            SUBMITTED[run_id] = task
            return self._reply(201, {"run": {"run_id": run_id,
                                             "status": "queued", "stages": []}})
        return self._reply(404, {"error": "not found"})

    def log_message(self, *args) -> None:
        pass


def request(method: str, url: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
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
        [sys.executable, "-m", "openroad_app_dse_lab",
         "--host", "127.0.0.1", "--port", str(app_port),
         "--kernel-url", f"http://127.0.0.1:{kernel_port}",
         "--db", str(APP_DIR / "smoke-dse.sqlite")],
        cwd=str(APP_DIR), env=env, stderr=subprocess.PIPE, text=True,
    )
    base = f"http://127.0.0.1:{app_port}"
    try:
        try:
            health = wait_for(f"{base}/health")
        except RuntimeError:
            if app.poll() is not None and app.stderr is not None:
                raise RuntimeError(
                    "the app exited before becoming healthy:\n" + app.stderr.read()
                ) from None
            raise
        assert health["app"] == "dse_lab", health

        status, created = request("POST", f"{base}/sweeps", {
            "name": "smoke sweep",
            "plugin_id": "example-reporter",
            "inputs": {"rtl_path": "/tmp/x.v", "platform": "nangate45"},
            "points": [
                {"core_utilization_pct": 40},
                {"core_utilization_pct": 55},
            ],
        })
        assert status == 201, created
        sweep_id = created["sweep"]["sweep_id"]
        assert len(created["sweep"]["points"]) == 2

        status, listing = request("GET", f"{base}/sweeps")
        assert status == 200 and len(listing["sweeps"]) == 1, listing

        status, detail = request("GET", f"{base}/sweeps/{sweep_id}")
        assert status == 200, detail
        # Every point was submitted and got a run back.
        assert all(p["run_id"] for p in detail["sweep"]["points"]), detail

        status, comparison = request("GET", f"{base}/sweeps/{sweep_id}/comparison")
        assert status == 200, comparison
        assert comparison["summary"]["points"] == 2, comparison["summary"]
        assert comparison["summary"]["succeeded"] == 2, comparison["summary"]
        assert comparison["summary"]["unsourced_metrics"] == 0
        assert all(row["metrics"] for row in comparison["points"])
        assert "claim_boundary" in comparison

        status, missing = request("GET", f"{base}/sweeps/sweep-absent")
        assert status == 404, missing

        print("dse_lab smoke: ok (create, list, detail, compare)")
        return 0
    finally:
        app.terminate()
        app.wait(timeout=10)
        kernel_server.shutdown()
        kernel_server.server_close()
        (APP_DIR / "smoke-dse.sqlite").unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
