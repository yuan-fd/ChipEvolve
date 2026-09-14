"""The integration entry point.

The gateway is the part that must stay boring.  These tests assert that it
routes, that it tells the truth about what is reachable, and that it contains
nothing worth growing.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

from openroad_platform_gateway import (
    AppRegistration,
    build_router,
    GatewayConfig,
    make_handler,
    probe,
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FakeApp:
    """A minimal real app process, so routing is exercised over a real socket."""

    def __init__(self, name: str):
        self.name = name
        self.port = free_port()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/health":
                    body = json.dumps({"app": outer.name, "status": "ok"}).encode()
                    status = 200
                elif self.path == "/echo":
                    body = json.dumps({"from": outer.name}).encode()
                    status = 200
                else:
                    body = json.dumps({"error": "not found"}).encode()
                    status = 404
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "FakeApp":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture()
def apps():
    started = [FakeApp("alpha").start(), FakeApp("beta").start()]
    yield started
    for app in started:
        app.stop()


@pytest.fixture()
def gateway(apps):
    config = GatewayConfig(apps=tuple(
        AppRegistration(name=a.name, base_url=a.base_url, title=a.name.title())
        for a in apps
    ))
    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(build_router(config)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", config
    server.shutdown()
    server.server_close()


def get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------

def test_config_comes_from_a_file_and_builds_the_navigation(tmp_path):
    config_path = tmp_path / "apps.json"
    config_path.write_text(json.dumps({"apps": [
        {"name": "rtl_studio", "base_url": "http://127.0.0.1:8801",
         "title": "RTL Studio", "description": "turn a spec into RTL"},
        {"name": "dse_lab", "base_url": "http://127.0.0.1:8802"},
    ]}), encoding="utf-8")

    config = GatewayConfig.from_file(config_path)
    nav = {row["name"]: row for row in config.nav()}
    assert nav["rtl_studio"]["title"] == "RTL Studio"
    assert nav["rtl_studio"]["path"] == "/app/rtl_studio/"
    # A title is derived when absent, so adding an app is one line of config.
    assert nav["dse_lab"]["title"] == "Dse Lab"


def test_a_duplicate_app_name_is_refused():
    with pytest.raises(ValueError, match="duplicate app name"):
        GatewayConfig.from_mapping({"apps": [
            {"name": "same", "base_url": "http://127.0.0.1:1"},
            {"name": "same", "base_url": "http://127.0.0.1:2"},
        ]})


def test_an_unknown_config_key_is_refused():
    with pytest.raises(ValueError, match="unknown gateway config keys"):
        GatewayConfig.from_mapping({"apps": [], "database": "gateway.db"})


def test_a_non_http_base_url_is_refused():
    with pytest.raises(ValueError, match="non-http base_url"):
        AppRegistration(name="x", base_url="file:///etc/passwd").validate()


def test_an_invalid_app_name_is_refused():
    with pytest.raises(ValueError, match="invalid app name"):
        AppRegistration(name="../escape", base_url="http://127.0.0.1:1").validate()


# --------------------------------------------------------------------------
# serving
# --------------------------------------------------------------------------

def test_health_reports_every_app_and_whether_it_answers(gateway, apps):
    base, _ = gateway
    status, payload = get(f"{base}/health")
    assert status == 200
    assert payload["service"] == "gateway"
    states = {row["name"]: row for row in payload["apps"]}
    assert set(states) == {"alpha", "beta"}
    assert all(row["reachable"] for row in states.values())
    assert states["alpha"]["health"]["status"] == "ok"


def test_an_unreachable_app_is_reported_not_hidden(gateway):
    base, config = gateway
    dead = AppRegistration(name="dead", base_url=f"http://127.0.0.1:{free_port()}")
    status, payload = get(f"{base}/health")  # keeps the endpoint exercised
    assert status == 200
    result = probe(dead, timeout=0.5)
    assert result["reachable"] is False
    assert "error" in result


def test_navigation_is_derived_from_the_app_list(gateway):
    base, config = gateway
    status, payload = get(f"{base}/apps")
    assert status == 200
    assert [row["name"] for row in payload["apps"]] == ["alpha", "beta"]


def test_routing_reaches_the_named_app(gateway):
    base, _ = gateway
    status, payload = get(f"{base}/app/beta/echo")
    assert status == 200
    assert payload["from"] == "beta"


def test_an_unknown_app_is_a_404(gateway):
    base, _ = gateway
    status, payload = get(f"{base}/app/nonexistent/echo")
    assert status == 404
    assert "unknown app" in payload["error"]


def test_an_unreachable_app_is_a_502_not_a_500(gateway):
    base, config = gateway
    # Rebuild the gateway with an app nothing is listening on.
    dead = AppRegistration(name="dead", base_url=f"http://127.0.0.1:{free_port()}")
    port = free_port()
    server = ThreadingHTTPServer(
        ("127.0.0.1", port),
        make_handler(build_router(GatewayConfig(apps=(dead,)))),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        time.sleep(0.05)
        status, payload = get(f"http://127.0.0.1:{port}/app/dead/echo")
        assert status == 502
        assert "unreachable" in payload["error"]
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------------
# the property that keeps the entry point thin
# --------------------------------------------------------------------------

def test_the_gateway_has_no_domain_logic_and_no_database():
    """A structural assertion about the thing that grew to 5,993 lines in v1.

    The gateway may not open a database, may not name a capability, and may not
    import the kernel.  If any of that changes, this fails before the file has a
    chance to become an application.
    """
    sources = [
        path for path in (Path(__file__).resolve().parents[1] / "gateway").rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    assert sources
    for path in sources:
        # ``bootstrap.py`` is the composition root and is the one file allowed
        # to name concrete kernel classes; it still may not open a database,
        # because the kernel owns its own store.
        if path.name == "bootstrap.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "sqlite3" not in text, f"{path.name} opens a database"
        assert "import openroad_platform_runtime" not in text
        assert "import openroad_platform_registry" not in text
        assert "import openroad_platform_evaluator" not in text
        assert "import openroad_platform_identity" not in text


# --------------------------------------------------------------------------
# the command line
# --------------------------------------------------------------------------

def test_the_worker_cli_runs_one_cycle(tmp_path):
    """``--once`` exists so a test, a cron job, or an operator can advance the
    queue without supervising a daemon."""
    state = tmp_path / "state"
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    state.mkdir()

    completed = subprocess.run(
        [sys.executable, "-m", "openroad_platform_gateway.worker",
         "--state-root", str(state), "--plugins-root", str(plugins),
         "--once", "--quiet"],
        capture_output=True, text=True, timeout=60,
        env={**__import__("os").environ,
             "PYTHONPATH": __import__("os").pathsep.join([
                 str(REPO_ROOT / "contracts" / "src"),
                 str(REPO_ROOT / "core" / "runtime" / "src"),
                 str(REPO_ROOT / "core" / "registry" / "src"),
                 str(REPO_ROOT / "core" / "evaluator" / "src"),
                 str(REPO_ROOT / "core" / "provenance" / "src"),
                 str(REPO_ROOT / "core" / "identity" / "src"),
                 str(REPO_ROOT / "gateway" / "src"),
             ])},
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout.strip().splitlines()[-1])
    assert report == {"reclaimed": 0, "cancelled": 0, "advanced": 0, "failed": 0}
    # The cycle created the store it was pointed at.
    assert (state / "runtime.db").is_file()
