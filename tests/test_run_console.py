"""Run Console against the real kernel.

The smoke uses a stub so it can run without a kernel.  This file does not: it
starts the kernel and a worker for real, lets the console submit a task, and
checks the evidence that comes back.  That is the difference between "the console
is wired correctly" and "the console's request actually became a measured run".

The behaviour under test is the console's only interesting one: it reports the
progress the kernel holds, and it does not invent any.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from openroad_platform_gateway import GatewayConfig, build_router, make_handler
from openroad_platform_gateway.bootstrap import KernelPaths, build_kernel
from openroad_platform_runtime import RuntimeWorker

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_ROOT = REPO_ROOT / "plugins"
ADMISSIONS_ROOT = REPO_ROOT / "admissions"
APP_DIR = REPO_ROOT / "apps" / "run_console"
CLIENT_SRC = REPO_ROOT / "core" / "client" / "src"

PASSWORD = "a long enough password"
TERMINAL = {"succeeded", "failed", "cancelled", "lost"}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def http(method: str, url: str, payload: dict | None = None,
         token: str | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def wait_for(url: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                return json.loads(response.read())
        except Exception as exc:  # noqa: BLE001 - retried until the deadline
            last = exc
            time.sleep(0.2)
    raise RuntimeError(f"{url} never became healthy: {last}")


@pytest.fixture()
def console(tmp_path: Path):
    """A real kernel with a worker, and the console running as its own process."""
    # A plugin root this test owns: the shipped plugins, plus one that cannot
    # start.  A platform test must not depend on a particular capability being
    # installed -- the capabilities live in their own repositories now.
    plugins = tmp_path / "plugins"
    shutil.copytree(PLUGINS_ROOT, plugins)
    broken = plugins / "cannot-start"
    broken.mkdir()
    (broken / "cannot-start.plugin.json").write_text(json.dumps({
        "schema_version": 3, "plugin_id": "cannot-start", "plugin_version": "1.0.0",
        "adapter_entry": ["/nonexistent/adapter"], "capabilities": ["never.runs"],
        "supported_arch": ["aarch64", "x86_64", "arm64"],
    }), encoding="utf-8")
    admissions = tmp_path / "admissions"
    shutil.copytree(ADMISSIONS_ROOT, admissions)
    (admissions / "cannot-start.json").write_text(json.dumps({
        "plugin_id": "cannot-start", "status": "admitted",
        "license_review": "green", "approved_commit": "0" * 40,
        "reviewer": "test", "reason": "admitted so its launch can fail",
    }), encoding="utf-8")

    kernel = build_kernel(KernelPaths.of(tmp_path / "state", plugins, admissions))
    router = build_router(GatewayConfig(), kernel)
    kernel_port = free_port()
    from http.server import ThreadingHTTPServer

    kernel_server = ThreadingHTTPServer(("127.0.0.1", kernel_port),
                                        make_handler(router))
    threading.Thread(target=kernel_server.serve_forever, daemon=True).start()

    worker = RuntimeWorker(kernel.store, kernel.runtime, idle_seconds=0.02)
    stop = threading.Event()
    worker_thread = threading.Thread(target=worker.serve_forever, args=(stop,),
                                     daemon=True)
    worker_thread.start()

    app_port = free_port()
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(APP_DIR / "src"), str(CLIENT_SRC)]
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
            wait_for(f"{base}/health")
        except RuntimeError:
            if app.poll() is not None and app.stderr is not None:
                raise RuntimeError("the app exited early:\n" + app.stderr.read())
            raise
        status, session = http(
            "POST", f"http://127.0.0.1:{kernel_port}/kernel/auth/register",
            {"username": "alice", "password": PASSWORD})
        assert status == 200, session
        yield base, session["token"]
    finally:
        app.terminate()
        app.wait(timeout=10)
        stop.set()
        worker_thread.join(timeout=5)
        kernel_server.shutdown()
        kernel_server.server_close()
        kernel.store.close()


def task(plugin_id: str) -> dict:
    return {
        "schema_version": 3, "task_id": f"task-{plugin_id}", "project_id": "p",
        "design_id": "d", "plugin_id": plugin_id, "inputs": {"records": [4, 9]},
        "parameters": {},
    }


def submit(base: str, token: str, plugin_id: str) -> str:
    status, created = http("POST", f"{base}/runs", {"task": task(plugin_id)},
                           token=token)
    assert status == 201, created
    return created["run"]["run_id"]


def wait_for_terminal(base: str, token: str, run_id: str,
                      timeout: float = 40.0) -> dict:
    deadline = time.monotonic() + timeout
    detail = http("GET", f"{base}/runs/{run_id}", token=token)[1]
    while time.monotonic() < deadline:
        detail = http("GET", f"{base}/runs/{run_id}", token=token)[1]
        if detail["run"]["status"] in TERMINAL:
            return detail
        time.sleep(0.1)
    return detail


# --------------------------------------------------------------------------
# submission
# --------------------------------------------------------------------------

def test_a_submitted_run_becomes_measured_evidence(console):
    base, token = console
    run_id = submit(base, token, "example-reporter")
    detail = wait_for_terminal(base, token, run_id)

    assert detail["run"]["status"] == "succeeded", detail["run"]
    # A measured run leaves artifacts and sourced metrics; that is what makes it
    # evidence rather than a status.
    assert detail["artifacts"], detail
    assert detail["evidence"]["artifacts"] == len(detail["artifacts"])
    assert detail["evidence"]["kinds"]
    assert detail["evidence"]["unsourced"] == 0, detail["evidence"]


def test_the_console_refuses_a_request_that_is_not_a_task(console):
    base, token = console
    status, refused = http("POST", f"{base}/runs", {"not_a_task": True},
                           token=token)
    assert status == 400, refused
    assert "task object is required" in refused["error"]


# --------------------------------------------------------------------------
# progress: what it says, and what it refuses to say
# --------------------------------------------------------------------------

def test_progress_reports_exactly_the_stages_the_kernel_holds(console):
    """The console's list is the kernel's events, not a view of its own.

    Stage names are the plugin's, carried through as opaque data: the console
    cannot enumerate them, so if this disagrees with the timeline something has
    been invented.
    """
    base, token = console
    run_id = submit(base, token, "example-reporter")
    detail = wait_for_terminal(base, token, run_id)

    from_events = [
        event["payload"]["stage"] for event in detail["timeline"]
        if event["event_type"] == "stage.started"
    ]
    reported = [stage["stage"] for stage in detail["progress"]["stages"]]
    assert reported == from_events, (reported, from_events)
    assert reported, "the example plugin reports stages; none were recorded"
    assert all(stage["status"] != "started" for stage in detail["progress"]["stages"])
    assert detail["progress"]["reported_nothing"] is False


def test_the_progress_payload_has_exactly_these_fields(console):
    """A progress view invites a number that is not known.

    There is no denominator: the console cannot know how many stages a plugin
    will report until it has reported them, so a percentage here would be
    invention presented as measurement.  The assertion is the exact field set
    rather than a search for suspicious words, because "no percentage" should
    mean an added field is a deliberate decision, not a substring nobody greps.
    """
    base, token = console
    run_id = submit(base, token, "example-reporter")
    detail = wait_for_terminal(base, token, run_id)

    assert set(detail["progress"]) == {
        "stages", "reported", "reported_nothing", "malformed_reports", "note"}
    for stage in detail["progress"]["stages"]:
        # ``seconds`` is what the plugin reported, not a duration the console
        # measured or predicted.
        assert set(stage) == {
            "stage", "status", "started_at", "finished_at", "seconds", "detail"}


def test_a_run_that_reported_nothing_says_so(console, tmp_path):
    """Absent stages are unreported, not zero.

    A task whose plugin cannot be launched fails before emitting any progress
    line, so the kernel holds no stage events for it.  The console must say
    that, rather than showing an empty bar that reads as "not started".
    """
    base, token = console
    status, created = http("POST", f"{base}/runs", {"task": {
        "schema_version": 3, "task_id": "cannot-start", "project_id": "p",
        "design_id": "d", "plugin_id": "cannot-start",
        "inputs": {}, "parameters": {},
    }}, token=token)
    assert status == 201, created
    run_id = created["run"]["run_id"]
    detail = wait_for_terminal(base, token, run_id)

    assert detail["run"]["status"] == "failed", detail["run"]
    assert detail["progress"]["reported"] == 0
    assert detail["progress"]["reported_nothing"] is True
    assert detail["progress"]["stages"] == []
    assert "unreported" in detail["progress"]["note"]

    # The run failed for a reason, and the reason survives to the reader.  The
    # adapter's own report is on the attempt: without it a caller sees "failed"
    # and cannot tell a missing toolchain from a crashed tool.
    attempts = [attempt for stage in detail["run"]["stages"]
                for attempt in stage["attempts"]]
    assert attempts, detail["run"]
    failure = attempts[-1]["failure"]
    assert failure, attempts
    # The platform could not start the plugin; the reason survives to the reader
    # rather than being reduced to "the run failed".
    assert failure["category"] == "runtime_error", failure
    assert failure["message"], failure


# --------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------

def test_the_console_forwards_the_callers_identity(console):
    """It owns no session, so it cannot show a caller more than the caller has.

    The point is the pairing: the console itself is up (``/health`` answers
    without a token) while the thing it reads still refuses.  A console that
    invented a session would answer 200 for both.
    """
    base, token = console
    status, health = http("GET", f"{base}/health")
    assert status == 200 and health["app"] == "run_console", health

    status, refused = http("GET", f"{base}/runs")
    assert status == 401, refused

    status, listing = http("GET", f"{base}/runs", token=token)
    assert status == 200, listing
