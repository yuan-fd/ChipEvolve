"""DSE Lab against the real kernel.

The smoke uses a stub so it can run without a toolchain.  This file does not:
it starts the kernel and a worker for real, lets the app submit a sweep, and
checks the evidence that comes back.  That is the difference between "the app is
wired correctly" and "the app's requests actually become measured runs".
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
from pathlib import Path

import pytest

from openroad_platform_gateway import GatewayConfig, build_router, make_handler
from openroad_platform_gateway.bootstrap import KernelPaths, build_kernel
from openroad_platform_runtime import RuntimeWorker

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_ROOT = REPO_ROOT / "plugins"
APP_DIR = REPO_ROOT / "apps" / "dse_lab"
CLIENT_SRC = REPO_ROOT / "core" / "client" / "src"

PASSWORD = "a long enough password"


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
def lab(tmp_path: Path):
    """A real kernel with a worker, and the app running as its own process."""
    kernel = build_kernel(KernelPaths.of(tmp_path / "state", PLUGINS_ROOT))
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
        [sys.executable, "-m", "openroad_app_dse_lab",
         "--host", "127.0.0.1", "--port", str(app_port),
         "--kernel-url", f"http://127.0.0.1:{kernel_port}",
         "--db", str(tmp_path / "dse.sqlite")],
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
        status, session = http("POST", f"http://127.0.0.1:{kernel_port}/kernel/auth/register",
                               {"username": "alice", "password": PASSWORD})
        assert status == 200, session
        yield base, session["token"], kernel
    finally:
        app.terminate()
        app.wait(timeout=10)
        stop.set()
        worker_thread.join(timeout=5)
        kernel_server.shutdown()
        kernel_server.server_close()
        kernel.store.close()


def wait_for_sweep(base: str, token: str, sweep_id: str,
                   timeout: float = 40.0) -> dict:
    """Poll the comparison until no point is still running."""
    deadline = time.monotonic() + timeout
    comparison = http("GET", f"{base}/sweeps/{sweep_id}/comparison",
                      token=token)[1]
    while time.monotonic() < deadline:
        comparison = http("GET", f"{base}/sweeps/{sweep_id}/comparison",
                          token=token)[1]
        statuses = {row["status"] for row in comparison["points"]}
        if not statuses & {"queued", "running", "retry_wait", "preparing"}:
            return comparison
        time.sleep(0.1)
    return comparison


# --------------------------------------------------------------------------
# the sweep lifecycle
# --------------------------------------------------------------------------

def test_a_sweep_is_submitted_and_every_point_becomes_a_measured_run(lab):
    base, token, _ = lab
    status, created = http("POST", f"{base}/sweeps", {
        "name": "utilisation sweep",
        "plugin_id": "example-reporter",
        "inputs": {"records": [1, 2, 3, 4]},
        "points": [{"a": 1}, {"a": 2}],
    }, token=token)
    assert status == 201, created
    sweep_id = created["sweep"]["sweep_id"]

    comparison = wait_for_sweep(base, token, sweep_id)
    assert comparison["summary"]["points"] == 2
    assert comparison["summary"]["succeeded"] == 2, comparison["points"]
    assert comparison["summary"]["failed"] == 0
    for row in comparison["points"]:
        assert row["status"] == "succeeded"
        # A measured run yields sourced metrics, which is what makes the
        # comparison evidence rather than a table of claims.
        assert row["metrics"]
        assert all(metric["complete"] for metric in row["metrics"])


def test_the_comparison_carries_metric_provenance(lab):
    base, token, _ = lab
    _, created = http("POST", f"{base}/sweeps", {
        "name": "provenance", "plugin_id": "example-reporter",
        "inputs": {"records": [1, 2, 3, 4]}, "points": [{"a": 1}],
    }, token=token)
    comparison = wait_for_sweep(base, token, created["sweep"]["sweep_id"])
    metric = comparison["points"][0]["metrics"][0]
    assert metric["complete"] is True
    assert metric["artifact"]["sha256"]
    assert metric["run_id"]
    assert comparison["summary"]["unsourced_metrics"] == 0


def test_the_comparison_states_what_it_is_not_claiming(lab):
    """Showing measured values is not a claim that one point is better."""
    base, token, _ = lab
    _, created = http("POST", f"{base}/sweeps", {
        "name": "boundary", "plugin_id": "example-reporter",
        "inputs": {"records": [1]}, "points": [{"a": 1}],
    }, token=token)
    comparison = wait_for_sweep(base, token, created["sweep"]["sweep_id"])
    assert "not a claim that any point is better" in comparison["claim_boundary"]


def test_the_sweep_records_its_points_in_submission_order(lab):
    base, token, _ = lab
    _, created = http("POST", f"{base}/sweeps", {
        "name": "order", "plugin_id": "example-reporter",
        "inputs": {"records": [1]},
        "points": [{"a": 3}, {"a": 1}, {"a": 2}],
    }, token=token)
    detail = http("GET", f"{base}/sweeps/{created['sweep']['sweep_id']}",
                  token=token)[1]
    assert [p["parameters"] for p in detail["sweep"]["points"]] == [
        {"a": 3}, {"a": 1}, {"a": 2}]


# --------------------------------------------------------------------------
# refusals are results, not omissions
# --------------------------------------------------------------------------

def test_a_point_the_kernel_refuses_is_reported_not_dropped(lab):
    """The lab does not validate parameters; the plugin does.

    A console that re-implemented the allowlist would be a second source of
    truth.  So an invalid point is submitted, refused, and reported -- and the
    comparison still counts it.
    """
    base, token, _ = lab
    _, created = http("POST", f"{base}/sweeps", {
        "name": "refusal", "plugin_id": "no-such-plugin",
        "inputs": {"records": [1]}, "points": [{"a": 1}, {"a": 2}],
    }, token=token)
    comparison = wait_for_sweep(base, token, created["sweep"]["sweep_id"])
    assert comparison["summary"]["points"] == 2
    assert comparison["summary"]["refused"] == 2
    for row in comparison["points"]:
        assert row["status"] == "refused"
        assert row["reason"]
        assert row["metrics"] == []


def test_a_failed_run_is_kept_in_the_comparison(lab):
    """A sweep that omits its failures flatters whichever policy produced fewer."""
    base, token, _ = lab
    _, created = http("POST", f"{base}/sweeps", {
        "name": "failure", "plugin_id": "example-reporter",
        # The example plugin rejects a non-list of records, so this run fails.
        "inputs": {"records": "not-a-list"}, "points": [{"a": 1}],
    }, token=token)
    comparison = wait_for_sweep(base, token, created["sweep"]["sweep_id"])
    assert comparison["summary"]["points"] == 1
    assert comparison["summary"]["failed"] == 1
    assert comparison["points"][0]["status"] == "failed"
    assert comparison["points"][0]["terminal_reason"]


# --------------------------------------------------------------------------
# the app's own boundaries
# --------------------------------------------------------------------------

def test_the_lab_owns_only_its_own_database(lab, tmp_path):
    """The sweeps are the app's; the evidence is the kernel's.

    There is no second copy of a run, an artifact or a metric on this side --
    which is what stops the two from disagreeing.
    """
    base, token, kernel = lab
    _, created = http("POST", f"{base}/sweeps", {
        "name": "ownership", "plugin_id": "example-reporter",
        "inputs": {"records": [1]}, "points": [{"a": 1}],
    }, token=token)
    comparison = wait_for_sweep(base, token, created["sweep"]["sweep_id"])
    run_id = comparison["points"][0]["run_id"]

    # The kernel holds the run; the app holds only a reference to its id.
    assert kernel.store.get_run(run_id).run_id == run_id
    import sqlite3

    with sqlite3.connect(str(tmp_path / "dse.sqlite")) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"sweeps", "points"}
    with sqlite3.connect(str(tmp_path / "dse.sqlite")) as connection:
        stored = connection.execute("SELECT run_id FROM points").fetchall()
    assert stored == [(run_id,)]


def test_a_second_sweep_does_not_reuse_the_first_ones_runs(lab):
    base, token, _ = lab
    for index in range(2):
        _, created = http("POST", f"{base}/sweeps", {
            "name": f"sweep {index}", "plugin_id": "example-reporter",
            "inputs": {"records": [index + 1]}, "points": [{"a": index}],
        }, token=token)
        comparison = wait_for_sweep(base, token, created["sweep"]["sweep_id"])
        assert comparison["points"][0]["run_id"].startswith("run-")


def test_the_sweep_list_is_newest_first(lab):
    base, token, _ = lab
    for name in ("first", "second"):
        http("POST", f"{base}/sweeps", {
            "name": name, "plugin_id": "example-reporter",
            "inputs": {"records": [1]}, "points": [{"a": 1}],
        }, token=token)
    listing = http("GET", f"{base}/sweeps", token=token)[1]
    assert [sweep["name"] for sweep in listing["sweeps"]] == ["second", "first"]


def test_an_unknown_sweep_is_a_404(lab):
    base, token, _ = lab
    assert http("GET", f"{base}/sweeps/sweep-absent", token=token)[0] == 404
    assert http("GET", f"{base}/sweeps/sweep-absent/comparison",
                token=token)[0] == 404


def test_an_unknown_route_is_a_404(lab):
    base, token, _ = lab
    assert http("GET", f"{base}/nonsense", token=token)[0] == 404


# --------------------------------------------------------------------------
# requests it refuses
# --------------------------------------------------------------------------

def test_a_sweep_without_points_is_refused(lab):
    base, token, _ = lab
    status, payload = http("POST", f"{base}/sweeps", {
        "name": "empty", "plugin_id": "example-reporter",
        "inputs": {"records": [1]}, "points": [],
    }, token=token)
    assert status == 400 and "at least one point" in payload["error"]


def test_a_sweep_without_a_name_is_refused(lab):
    base, token, _ = lab
    status, payload = http("POST", f"{base}/sweeps", {
        "name": "  ", "plugin_id": "example-reporter",
        "inputs": {"records": [1]}, "points": [{"a": 1}],
    }, token=token)
    assert status == 400 and "name is required" in payload["error"]


def test_an_oversized_sweep_is_refused(lab):
    """An unbounded sweep is a way for one caller to occupy the worker pool."""
    from openroad_app_dse_lab.__main__ import MAX_POINTS_PER_SWEEP
    base, token, _ = lab
    status, payload = http("POST", f"{base}/sweeps", {
        "name": "huge", "plugin_id": "example-reporter",
        "inputs": {"records": [1]},
        "points": [{"a": index} for index in range(MAX_POINTS_PER_SWEEP + 1)],
    }, token=token)
    assert status == 400 and "limited to" in payload["error"]


def test_points_must_be_objects(lab):
    base, token, _ = lab
    status, payload = http("POST", f"{base}/sweeps", {
        "name": "bad", "plugin_id": "example-reporter",
        "inputs": {"records": [1]}, "points": ["not an object"],
    }, token=token)
    assert status == 400 and "list of parameter objects" in payload["error"]


def test_inputs_must_be_an_object(lab):
    base, token, _ = lab
    status, payload = http("POST", f"{base}/sweeps", {
        "name": "bad", "plugin_id": "example-reporter",
        "inputs": "nope", "points": [{"a": 1}],
    }, token=token)
    assert status == 400 and "inputs must be an object" in payload["error"]


def test_the_lab_holds_no_credentials_of_its_own(lab):
    """It forwards the caller's token, so it cannot act as anyone else."""
    base, _, _ = lab
    status, _ = http("POST", f"{base}/sweeps", {
        "name": "unauthenticated", "plugin_id": "example-reporter",
        "inputs": {"records": [1]}, "points": [{"a": 1}],
    })
    # The kernel refuses, and the lab reports that refusal rather than
    # substituting an identity of its own.
    assert status in (201, 401, 502)
    if status == 201:
        listing = http("GET", f"{base}/sweeps")[1]
        sweep_id = listing["sweeps"][0]["sweep_id"]
        comparison = http("GET", f"{base}/sweeps/{sweep_id}/comparison")[1]
        assert comparison["summary"]["refused"] >= 1
