#!/usr/bin/env python3
"""Scaffold a compliant app.

The point of this script is to make the *correct* path the *easiest* path.  A
guardrail that only forbids is a wall; an agent facing a wall will tunnel
through the nearest existing file instead.  This gives it a door.

Usage:
    python3 tools/new_app.py <app_name> [--title "Human Title"]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APPS_DIR = REPO_ROOT / "apps"

NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

PYPROJECT = """[build-system]
requires = ["setuptools>=59.4"]
build-backend = "setuptools.build_meta"

[project]
name = "openroad-app-{name}"
version = "0.1.0"
description = "{title}"
requires-python = ">=3.9"
dependencies = [
  "openroad-platform-contracts",
]

[project.scripts]
openroad-app-{name} = "openroad_app_{name}.__main__:main"

[tool.setuptools.packages.find]
where = ["src"]
include = ["openroad_app_{name}*"]
"""

MAIN = '''"""{title} - process entry point.

This module starts the app's own server.  It does not import another app, and
it does not import kernel internals: only `openroad_platform_contracts` and the core
client are allowed (G3, G4).
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SERVICE_NAME = "{name}"


class Handler(BaseHTTPRequestHandler):
    server_version = "openroad-app-{name}/0.1"

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self.send_error(404)
            return
        body = json.dumps({{"app": SERVICE_NAME, "status": "ok"}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # noqa: D102
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="{title}")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default={port})
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

SMOKE = '''"""{title} - end-to-end smoke.

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
DEFAULT_PORT = {port}


def wait_for_health(port: int, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{{port}}/health", timeout=1
            ) as response:
                return json.loads(response.read())
        except Exception as exc:  # noqa: BLE001 - retried until the deadline
            last = exc
            time.sleep(0.2)
    raise RuntimeError(f"{{APP_DIR.name}} did not become healthy: {{last}}")


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    # The app is a real process with its own source root.  Pass an absolute
    # path so the child resolves it regardless of its working directory.
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(SRC_DIR)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "openroad_app_{name}.__main__",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(APP_DIR),
        env=env,
    )
    try:
        payload = wait_for_health(port)
        assert payload["app"] == "{name}", payload
        assert payload["status"] == "ok", payload
        print(f"{name} smoke: ok")
        return 0
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
'''

README = """# {title}

Independent application. One process, one database, one UI, one smoke.

- Imports permitted: `openroad_platform_contracts`, `openroad_platform_client` only (G3, G4).
- It must never open a kernel database directly (G5).
- Smoke: `python3 apps/{name}/smoke.py`

## Why this app exists

<fill in the single capability this app owns>

## What it must not do

<fill in the neighbouring concerns this app deliberately does not own>
"""


def next_free_port() -> int:
    used = set()
    for path in APPS_DIR.glob("*/smoke.py"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"DEFAULT_PORT = (\d+)", text):
            used.add(int(match.group(1)))
    port = 8800
    while port in used:
        port += 1
    return port


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument("--title", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    if not NAME_RE.match(args.name):
        print(f"error: {args.name!r} must be lower_snake_case", file=sys.stderr)
        return 2

    app_dir = APPS_DIR / args.name
    if app_dir.exists():
        print(f"error: {app_dir} already exists", file=sys.stderr)
        return 2

    title = args.title or args.name.replace("_", " ").title()
    port = args.port or next_free_port()
    package_dir = app_dir / "src" / f"openroad_app_{args.name}"
    package_dir.mkdir(parents=True)

    (app_dir / "pyproject.toml").write_text(
        PYPROJECT.format(name=args.name, title=title), encoding="utf-8")
    (app_dir / "smoke.py").write_text(
        SMOKE.format(name=args.name, title=title, port=port), encoding="utf-8")
    (app_dir / "README.md").write_text(
        README.format(name=args.name, title=title), encoding="utf-8")
    (package_dir / "__init__.py").write_text(
        f'"""{title}."""\n', encoding="utf-8")
    (package_dir / "__main__.py").write_text(
        MAIN.format(name=args.name, title=title, port=port), encoding="utf-8")

    print(f"created apps/{args.name} (port {port})")
    print(f"  next: implement the capability, then run "
          f"`python3 apps/{args.name}/smoke.py`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
