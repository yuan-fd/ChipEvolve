"""Run Console - end-to-end smoke.

G9 requires every app to prove it starts as a real process.  This one needs a
kernel, so the smoke starts a stub that answers the calls the console makes,
then drives the whole path over HTTP: submit, list, follow, cancel.

A stub rather than the real kernel on purpose: this smoke is about the console
being wired correctly and should not fail because a plugin is missing.  The real
composition, with a live worker executing the submitted run, is covered by
``tests/test_run_console.py``.

The stub records two stage events for the submitted run, one of them finished,
so the smoke can assert the console's actual behaviour: it reports what the
kernel holds, and it does not invent the stages that were never reported.
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
DEFAULT_PORT = 8830

#: What the stub kernel remembers about a submitted task.
SUBMITTED: dict[str, dict] = {}

#: The timeline the stub reports: one stage started and finished, one still
#: running, and one malformed envelope.  Nothing here is a guess by the console.
TIMELINE = [
    {"event_type": "stage.started", "occurred_at": "2026-01-01T00:00:00+00:00",
     "producer": "orfs", "payload": {"stage": "synthesize"},
     "stage_run_id": "stage-1", "attempt_id": "attempt-1"},
    {"event_type": "stage.finished", "occurred_at": "2026-01-01T00:00:09+00:00",
     "producer": "orfs",
     "payload": {"stage": "synthesize", "status": "succeeded", "seconds": 9.0},
     "stage_run_id": "stage-1", "attempt_id": "attempt-1"},
    {"event_type": "stage.started", "occurred_at": "2026-01-01T00:00:09+00:00",
     "producer": "orfs", "payload": {"stage": "place"},
     "stage_run_id": "stage-2", "attempt_id": "attempt-1"},
    {"event_type": "progress.malformed", "occurred_at": "2026-01-01T00:00:10+00:00",
     "producer": "orfs", "payload": {"count": 2, "marker": "[progress]"},
     "stage_run_id": "stage-2", "attempt_id": "attempt-1"},
]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class StubKernel(BaseHTTPRequestHandler):
    """Just enough kernel: health, submit, read a run, its timeline and evidence."""

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/kernel/health":
            return self._reply(200, {"service": "kernel", "status": "ok"})
        if path == "/kernel/plugins":
            return self._reply(200, {"plugins": [
                {"plugin_id": "orfs", "admitted": True}]})
        if path == "/kernel/runs":
            return self._reply(200, {"runs": [
                {"run_id": run_id, "status": "running", "plugin_id": "orfs"}
                for run_id in SUBMITTED]})
        if path.endswith("/timeline"):
            run_id = path[len("/kernel/runs/"):-len("/timeline")]
            if run_id not in SUBMITTED:
                return self._reply(404, {"error": "unknown run"})
            return self._reply(200, {"timeline": TIMELINE})
        if path.endswith("/artifacts"):
            return self._reply(200, {"artifacts": [
                {"artifact_id": "art-1", "kind": "odb",
                 "path": "results/base/1_synth.odb", "sha256": "0" * 64}]})
        if path.endswith("/metrics"):
            return self._reply(200, {"metrics": [
                {"name": "core_utilization_pct", "value": 40.0, "complete": True,
                 "artifact": {"artifact_id": "art-1", "sha256": "0" * 64}},
                {"name": "unsourced_thing", "value": 1.0, "complete": False,
                 "artifact": None},
            ]})
        if path.startswith("/kernel/runs/"):
            run_id = path[len("/kernel/runs/"):]
            if run_id not in SUBMITTED:
                return self._reply(404, {"error": "unknown run"})
            return self._reply(200, {"run": {
                "run_id": run_id, "status": "running", "stages": [],
                "terminal_reason": None}})
        return self._reply(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else {}
        path = self.path.split("?")[0]
        if path == "/kernel/runs":
            task = body["task"]
            run_id = f"run-{len(SUBMITTED) + 1}"
            SUBMITTED[run_id] = task
            return self._reply(201, {"run": {"run_id": run_id,
                                             "status": "queued", "stages": []}})
        if path.startswith("/kernel/runs/") and path.endswith("/cancel"):
            run_id = path[len("/kernel/runs/"):-len("/cancel")]
            if run_id not in SUBMITTED:
                return self._reply(404, {"error": "unknown run"})
            return self._reply(200, {"run": {"run_id": run_id,
                                             "status": "cancelled"}})
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
        [sys.executable, "-m", "openroad_app_run_console",
         "--host", "127.0.0.1", "--port", str(app_port),
         "--kernel-url", f"http://127.0.0.1:{kernel_port}"],
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
        assert health["app"] == "run_console", health

        status, created = request("POST", f"{base}/runs", {
            "task": {"schema_version": 3, "task_id": "t-1", "project_id": "p",
                     "design_id": "gcd", "plugin_id": "orfs",
                     "inputs": {"rtl_path": "/tmp/x.v", "platform": "nangate45"},
                     "parameters": {}},
        })
        assert status == 201, created
        run_id = created["run"]["run_id"]

        status, listing = request("GET", f"{base}/runs")
        assert status == 200 and listing["count"] == 1, listing

        status, detail = request("GET", f"{base}/runs/{run_id}")
        assert status == 200, detail

        # The console reports the stages the kernel holds, in arrival order.
        stages = detail["progress"]["stages"]
        assert [stage["stage"] for stage in stages] == ["synthesize", "place"], stages
        assert stages[0]["status"] == "succeeded", stages[0]
        assert stages[0]["seconds"] == 9.0, stages[0]
        # The last one started and never finished: running, not failed, not done.
        assert stages[1]["status"] == "running", stages[1]
        assert stages[1]["finished_at"] is None, stages[1]
        # A malformed envelope is counted, not smoothed away.
        assert detail["progress"]["malformed_reports"] == 2, detail["progress"]
        assert detail["progress"]["reported_nothing"] is False
        # Nothing was invented: there is no percentage or estimate anywhere.
        assert "percent" not in json.dumps(detail["progress"])
        # A metric that cites no artifact is reported as unsourced, not hidden.
        assert detail["evidence"]["unsourced"] == 1, detail["evidence"]
        assert detail["evidence"]["kinds"] == ["odb"], detail["evidence"]

        status, cancelled = request("POST", f"{base}/runs/{run_id}/cancel")
        assert status == 200, cancelled
        assert cancelled["run"]["status"] == "cancelled", cancelled

        status, refused = request("POST", f"{base}/runs", {"not_a_task": True})
        assert status == 400, refused

        status, missing = request("GET", f"{base}/runs/run-absent")
        assert status == 404, missing

        print("run_console smoke: ok (submit, list, follow, cancel)")
        return 0
    finally:
        app.terminate()
        app.wait(timeout=10)
        kernel_server.shutdown()
        kernel_server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
