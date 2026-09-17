"""Execution Plan Service - end-to-end smoke.

G9 requires every app to prove it actually starts as a real process.  A unit
test that imports a module is not a smoke.  This starts the server, calls it
over HTTP, and asserts the response.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
SRC_DIR = APP_DIR / "src"
DEFAULT_PORT = 8840


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


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    # The app is a real process with its own source root.  Pass an absolute
    # path so the child resolves it regardless of its working directory.
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SRC_DIR)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "openroad_app_plan_executor.__main__",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(APP_DIR),
        env=env,
    )
    try:
        payload = wait_for_health(port)
        assert payload["app"] == "plan_executor", payload
        assert payload["status"] == "ok", payload
        print("plan_executor smoke: ok")
        return 0
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
