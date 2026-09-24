"""Execution Plan Service end-to-end process smoke."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
SRC_DIR = APP_DIR / "src"
CLIENT_SRC = APP_DIR.parents[1] / "core" / "client" / "src"
CONTRACTS_SRC = APP_DIR.parents[1] / "contracts" / "src"
DEFAULT_PORT = 8840
SUBMITTED: list[dict] = []


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class StubKernel(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/kernel/health":
            return self._reply(200, {"service": "kernel", "status": "ok"})
        if path.startswith("/kernel/runs/") and path.endswith("/artifacts"):
            run_id = path[len("/kernel/runs/"):-len("/artifacts")]
            artifacts = ([{"artifact_id": "artifact-report", "kind": "report",
                           "sha256": "a" * 64, "metadata": {}}]
                         if run_id == "run-1" else [])
            return self._reply(200, {"artifacts": artifacts})
        if path.startswith("/kernel/runs/"):
            run_id = path[len("/kernel/runs/"):]
            return self._reply(200, {"run": {
                "run_id": run_id, "status": "succeeded",
                "terminal_reason": None, "stages": [],
            }})
        return self._reply(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else {}
        if path == "/kernel/runs":
            SUBMITTED.append(body["task"])
            run_id = f"run-{len(SUBMITTED)}"
            return self._reply(201, {"run": {
                "run_id": run_id, "status": "queued", "stages": [],
            }})
        return self._reply(404, {"error": "not found"})

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def request(method: str, url: str, payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode() if payload is not None else None
    call = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(call, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def wait_for(url: str, *, status: str | None = None,
             timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            payload = request("GET", url)[1]
            if status is None or payload.get("plan", {}).get("status") == status:
                return payload
        except Exception as exc:  # noqa: BLE001 - bounded readiness polling
            last = exc
        time.sleep(0.1)
    raise RuntimeError(f"{url} did not become ready: {last}")


def task(task_id: str) -> dict:
    return {
        "schema_version": 3, "task_id": task_id, "project_id": "p",
        "design_id": "d", "plugin_id": "example-reporter", "inputs": {},
    }


def main() -> int:
    # A smoke must be repeatable while a developer has another local instance
    # running.  Use the documented port when explicitly supplied, otherwise
    # ask the OS for an available one instead of racing a stale process.
    app_port = int(sys.argv[1]) if len(sys.argv) > 1 else free_port()
    kernel_port = free_port()
    kernel = ThreadingHTTPServer(("127.0.0.1", kernel_port), StubKernel)
    threading.Thread(target=kernel.serve_forever, daemon=True).start()
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SRC_DIR), str(CLIENT_SRC)]
        + [str(CONTRACTS_SRC)]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    with tempfile.TemporaryDirectory() as temporary:
        app = subprocess.Popen(
            [sys.executable, "-m", "openroad_app_plan_executor",
             "--host", "127.0.0.1", "--port", str(app_port),
             "--kernel-url", f"http://127.0.0.1:{kernel_port}",
             "--db", str(Path(temporary) / "plans.sqlite")],
            cwd=str(APP_DIR), env=env, stderr=subprocess.PIPE, text=True,
        )
        base = f"http://127.0.0.1:{app_port}"
        try:
            wait_for(f"{base}/health")
            status, created = request("POST", f"{base}/plans", {
                "plan_id": "smoke-plan",
                "steps": [
                    {"step_id": "produce", "task": task("produce")},
                    {"step_id": "consume", "task": task("consume"),
                     "bindings": [{
                         "from_step": "produce", "artifact_kind": "report",
                         "destination": "inputs/report.json",
                     }]},
                ],
            })
            assert status == 201, created
            finished = wait_for(
                f"{base}/plans/smoke-plan", status="succeeded"
            )["plan"]
            assert finished["execution_valid"] is True, finished
            assert [step["run_id"] for step in finished["steps"]] == [
                "run-1", "run-2"]
            assert SUBMITTED[1]["staged_inputs"][0]["artifact_id"] == (
                "artifact-report"
            )
            print("plan_executor smoke: ok (ordered runs, artifact binding)")
            return 0
        finally:
            app.terminate()
            app.wait(timeout=10)
            kernel.shutdown()
            kernel.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
