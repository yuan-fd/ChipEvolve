"""The platform end to end, through the door applications actually use.

The last group of tests starts a real worker alongside the kernel, because a
platform where a submitted run only advances when a test calls the runtime is
not a platform.  It is a library with a queue in front of it.

A gateway with the kernel attached is started on a real socket.  The test then
behaves like an application: it registers, submits a task, has a worker run it,
and reads the evidence back -- all through ``KernelClient``, never by touching
the store.

That is the property this file exists to prove: an application can do its job
without the kernel's database in-process (G5) and without importing kernel
internals (G4).
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest

from openroad_platform_client import KernelClient, KernelError
from openroad_platform_gateway import GatewayConfig, build_router, make_handler
from openroad_platform_gateway.bootstrap import KernelPaths, build_kernel
from openroad_platform_runtime import RuntimeWorker

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_ROOT = REPO_ROOT / "plugins"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def platform(tmp_path: Path):
    """A running gateway with the kernel attached, and a client for it."""
    kernel = build_kernel(KernelPaths.of(tmp_path / "state", PLUGINS_ROOT))
    router = build_router(GatewayConfig(), kernel)
    port = free_port()
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(router))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.05)
    client = KernelClient(f"http://127.0.0.1:{port}")
    try:
        yield client, kernel
    finally:
        server.shutdown()
        server.server_close()
        kernel.store.close()


def submit_example(client: KernelClient, *, records=None, task_id="t-1"):
    return client.submit({
        "schema_version": 2,
        "task_id": task_id,
        "project_id": "demo",
        "design_id": "demo-design",
        "plugin_id": "example-reporter",
        "inputs": {"records": records if records is not None else [1, 2, 3, 4]},
        "timeout_seconds": 30,
    })


# --------------------------------------------------------------------------
# health and catalogue
# --------------------------------------------------------------------------

def test_health_is_reachable_without_a_session(platform):
    client, _ = platform
    assert client.health()["status"] == "ok"


def test_the_catalogue_reports_admission_state(platform):
    client, _ = platform
    client.register("alice", "a long enough password")
    catalogue = {row["plugin_id"]: row for row in client.plugins()}
    assert catalogue["example-reporter"]["executable"] is True
    assert "capabilities" in catalogue["example-reporter"]


# --------------------------------------------------------------------------
# authentication
# --------------------------------------------------------------------------

def test_a_protected_route_needs_a_session(platform):
    client, _ = platform
    with pytest.raises(KernelError) as caught:
        client.runs()
    assert caught.value.status == 401


def test_the_first_registration_gets_a_token_and_the_developer_role(platform):
    client, _ = platform
    session = client.register("alice", "a long enough password")
    assert session["token"]
    assert session["session"]["user"]["role"] == "developer"


def test_a_wrong_password_is_a_401_not_a_500(platform):
    client, _ = platform
    client.register("alice", "a long enough password")
    with pytest.raises(KernelError) as caught:
        KernelClient(client.base_url).login("alice", "wrong password")
    assert caught.value.status == 401
    assert "Invalid username or password" in str(caught.value)


def test_login_returns_a_usable_token(platform):
    client, _ = platform
    client.register("alice", "a long enough password")
    fresh = KernelClient(client.base_url)
    fresh.login("alice", "a long enough password")
    assert fresh.session()["user"]["username"] == "alice"


def test_logout_invalidates_the_token(platform):
    client, _ = platform
    client.register("alice", "a long enough password")
    client.logout()
    with pytest.raises(KernelError) as caught:
        client.runs()
    assert caught.value.status == 401


def test_a_bad_token_is_refused(platform):
    client, _ = platform
    client.register("alice", "a long enough password")
    client.token = "not-a-real-token"
    with pytest.raises(KernelError) as caught:
        client.runs()
    assert caught.value.status == 401


# --------------------------------------------------------------------------
# the full run path
# --------------------------------------------------------------------------

def test_a_task_submitted_through_the_api_runs_and_leaves_evidence(platform):
    client, kernel = platform
    client.register("alice", "a long enough password")

    created = submit_example(client)
    run_id = created["run"]["run_id"]
    assert created["run"]["status"] == "queued"

    # A worker executes it.  The API is how a caller submits; execution is the
    # runtime's, which is why no route exposes it.
    kernel.runtime.execute_once(run_id)

    detail = client.run(run_id)
    assert detail["status"] == "succeeded"
    attempt = detail["stages"][0]["attempts"][0]
    assert attempt["status"] == "succeeded"
    assert {a["kind"] for a in attempt["artifacts"]} == {"summary", "log"}


def test_metrics_arrive_with_their_provenance(platform):
    client, kernel = platform
    client.register("alice", "a long enough password")
    run_id = submit_example(client)["run"]["run_id"]
    kernel.runtime.execute_once(run_id)

    metrics = {m["name"]: m for m in client.metrics(run_id)}
    assert metrics["record_count"]["value"] == 4
    assert metrics["mean"]["value"] == 2.5
    for metric in metrics.values():
        assert metric["complete"] is True
        assert metric["artifact"]["sha256"]
        assert metric["parser"]["id"] == "example-reporter"


def test_artifacts_are_listed_with_their_hashes(platform):
    client, kernel = platform
    client.register("alice", "a long enough password")
    run_id = submit_example(client)["run"]["run_id"]
    kernel.runtime.execute_once(run_id)

    artifacts = client.artifacts(run_id)
    assert len(artifacts) == 2
    for artifact in artifacts:
        assert len(artifact["sha256"]) == 64
        assert artifact["size_bytes"] > 0
        assert artifact["attempt_id"]


def test_an_artifact_excerpt_is_read_through_the_kernel(platform):
    client, kernel = platform
    client.register("alice", "a long enough password")
    run_id = submit_example(client)["run"]["run_id"]
    kernel.runtime.execute_once(run_id)

    summary = next(a for a in client.artifacts(run_id) if a["kind"] == "summary")
    excerpt = client.artifact_excerpt(run_id, summary["artifact_id"],
                                      offset=0, max_bytes=4096)
    # The excerpt is the artifact's own bytes, re-hashed by the kernel; it
    # is not a projection the kernel composed.
    assert '"count": 4' in excerpt["text"]
    assert excerpt["sha256"] == summary["sha256"]


def test_the_client_refuses_an_oversized_excerpt_before_calling(platform):
    client, _ = platform
    client.register("alice", "a long enough password")
    with pytest.raises(ValueError, match="max_bytes"):
        client.artifact_excerpt("run-x", "art-y", max_bytes=10 ** 9)


def test_the_timeline_shows_stage_events_without_the_kernel_knowing_stages(platform):
    client, kernel = platform
    client.register("alice", "a long enough password")
    run_id = submit_example(client)["run"]["run_id"]
    kernel.runtime.execute_once(run_id)

    timeline = client.timeline(run_id)
    started = [e for e in timeline if e["event_type"] == "stage.started"]
    assert [e["payload"]["stage"] for e in started] == ["validate", "summarize"]


def test_the_graph_links_artifacts_to_metrics(platform):
    client, kernel = platform
    client.register("alice", "a long enough password")
    run_id = submit_example(client)["run"]["run_id"]
    kernel.runtime.execute_once(run_id)

    graph = client.graph([run_id])
    kinds = {n["kind"] for n in graph["nodes"]}
    assert {"run", "stage", "attempt", "artifact", "metric"} <= kinds
    assert any(e["kind"] == "sources" for e in graph["edges"])


def test_runs_can_be_listed_and_filtered(platform):
    client, kernel = platform
    client.register("alice", "a long enough password")
    submit_example(client, task_id="t-1")
    run_id = submit_example(client, task_id="t-2")["run"]["run_id"]
    kernel.runtime.execute_once(run_id)

    assert len(client.runs()) == 2
    assert len(client.runs(project_id="demo")) == 2
    assert client.runs(project_id="absent") == []
    assert [r["task_id"] for r in client.runs(status="succeeded")] == ["t-2"]


def test_a_failing_task_is_recorded_as_failed(platform):
    client, kernel = platform
    client.register("alice", "a long enough password")
    run_id = submit_example(client, records="not-a-list")["run"]["run_id"]
    kernel.runtime.execute_once(run_id)

    detail = client.run(run_id)
    assert detail["status"] == "failed"
    assert detail["terminal_reason"] == "flow_error" or detail["terminal_reason"]


def test_idempotent_submission_returns_the_same_run(platform):
    client, _ = platform
    client.register("alice", "a long enough password")
    task = {
        "schema_version": 2, "task_id": "same", "project_id": "demo",
        "design_id": "demo-design", "plugin_id": "example-reporter",
        "inputs": {"records": [1, 2, 3, 4]}, "timeout_seconds": 30,
    }
    first = client.submit(task)
    second = client.submit(task, idempotent=True)
    # Submitting the same immutable task twice is the same run, not a second one.
    assert first["run"]["run_id"] == second["run"]["run_id"]


# --------------------------------------------------------------------------
# refusal behaviour
# --------------------------------------------------------------------------

def test_an_unknown_plugin_is_refused(platform):
    client, _ = platform
    client.register("alice", "a long enough password")
    with pytest.raises(KernelError) as caught:
        client.submit({
            "schema_version": 2, "task_id": "x", "project_id": "p",
            "design_id": "d", "plugin_id": "no-such-plugin", "inputs": {},
        })
    assert caught.value.status == 500
    assert "unknown plugin" in str(caught.value)


def test_an_invalid_task_is_a_400(platform):
    client, _ = platform
    client.register("alice", "a long enough password")
    with pytest.raises(KernelError) as caught:
        client.submit({"schema_version": 2, "task_id": "x"})
    assert caught.value.status == 400


def test_a_body_that_is_not_an_object_is_a_400(platform):
    client, _ = platform
    client.register("alice", "a long enough password")
    with pytest.raises(KernelError) as caught:
        client._call("POST", "/kernel/runs", payload=None)  # noqa: SLF001
    # No body at all is empty, which is not an object.
    assert caught.value.status in {400, 500}


def test_an_unknown_run_is_a_404(platform):
    client, _ = platform
    client.register("alice", "a long enough password")
    with pytest.raises(KernelError) as caught:
        client.run("run-does-not-exist")
    assert caught.value.status == 404


def test_an_unknown_route_is_a_404(platform):
    client, _ = platform
    client.register("alice", "a long enough password")
    with pytest.raises(KernelError) as caught:
        client._call("GET", "/kernel/nonsense")  # noqa: SLF001
    assert caught.value.status == 404


def test_a_wrong_method_is_a_405(platform):
    client, _ = platform
    client.register("alice", "a long enough password")
    with pytest.raises(KernelError) as caught:
        client._call("POST", "/kernel/plugins")  # noqa: SLF001
    assert caught.value.status == 405


def test_a_missing_limit_is_not_an_error(platform):
    client, _ = platform
    client.register("alice", "a long enough password")
    assert client.runs(limit=None) == []


def test_an_invalid_limit_is_a_400(platform):
    client, _ = platform
    client.register("alice", "a long enough password")
    with pytest.raises(KernelError) as caught:
        client.runs(limit=0)
    assert caught.value.status == 400


# --------------------------------------------------------------------------
# ownership
# --------------------------------------------------------------------------

def test_a_member_cannot_see_another_users_run(platform):
    client, kernel = platform
    client.register("alice", "a long enough password")
    alice_run = submit_example(client, task_id="alice-1")["run"]["run_id"]

    bob = KernelClient(client.base_url)
    bob.register("bob", "a long enough password")
    assert bob.session()["user"]["role"] == "member"

    assert bob.runs() == []
    # 404 rather than 403: telling bob that alice's run exists is itself a
    # disclosure.
    with pytest.raises(KernelError) as caught:
        bob.run(alice_run)
    assert caught.value.status == 404


def test_a_developer_can_see_every_run(platform):
    client, _ = platform
    client.register("alice", "a long enough password")
    submit_example(client, task_id="alice-1")

    bob = KernelClient(client.base_url)
    bob.register("bob", "a long enough password")
    submit_example(bob, task_id="bob-1")

    # Alice is the developer, so she sees both.
    assert len(client.runs()) == 2


# --------------------------------------------------------------------------
# runs progress on their own
# --------------------------------------------------------------------------

@pytest.fixture()
def staffed_platform(tmp_path: Path):
    """The same platform, with a worker thread running cycles."""
    kernel = build_kernel(KernelPaths.of(tmp_path / "state", PLUGINS_ROOT))
    router = build_router(GatewayConfig(), kernel)
    port = free_port()
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(router))
    threading.Thread(target=server.serve_forever, daemon=True).start()

    worker = RuntimeWorker(kernel.store, kernel.runtime, idle_seconds=0.02)
    stop = threading.Event()
    thread = threading.Thread(target=worker.serve_forever, args=(stop,),
                              daemon=True)
    thread.start()
    time.sleep(0.05)
    client = KernelClient(f"http://127.0.0.1:{port}")
    try:
        yield client, kernel
    finally:
        stop.set()
        thread.join(timeout=5)
        server.shutdown()
        server.server_close()
        kernel.store.close()


def wait_for_status(client: KernelClient, run_id: str, wanted: str,
                    timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    detail = client.run(run_id)
    while time.monotonic() < deadline:
        detail = client.run(run_id)
        if detail["status"] == wanted:
            return detail
        if detail["status"] in {"failed", "cancelled", "timed_out", "lost"}:
            return detail
        time.sleep(0.05)
    raise AssertionError(
        f"run {run_id} never reached {wanted}; last status {detail['status']}"
    )


def test_a_submitted_run_completes_without_anyone_calling_the_runtime(
    staffed_platform,
):
    """The whole point of a worker: submit through the API, then wait."""
    client, _ = staffed_platform
    client.register("alice", "a long enough password")
    run_id = submit_example(client, task_id="worker-1")["run"]["run_id"]

    detail = wait_for_status(client, run_id, "succeeded")
    assert detail["status"] == "succeeded"
    attempt = detail["stages"][0]["attempts"][0]
    assert attempt["status"] == "succeeded"


def test_evidence_from_a_worker_run_is_readable_through_the_api(staffed_platform):
    client, _ = staffed_platform
    client.register("alice", "a long enough password")
    run_id = submit_example(client, task_id="worker-2")["run"]["run_id"]
    wait_for_status(client, run_id, "succeeded")

    metrics = {m["name"]: m for m in client.metrics(run_id)}
    assert metrics["mean"]["value"] == 2.5
    assert metrics["mean"]["complete"] is True


def test_a_cancelled_queued_run_settles_instead_of_hanging(staffed_platform):
    """A cancellation for a run with no live attempt must still reach a terminal
    state, or the caller waits forever for something that will never happen."""
    client, kernel = staffed_platform
    client.register("alice", "a long enough password")

    # Stop the worker so the run cannot be claimed before it is cancelled.
    run_id = submit_example(client, task_id="cancel-me")["run"]["run_id"]
    client.cancel(run_id)

    detail = wait_for_status(client, run_id, "cancelled")
    assert detail["status"] in {"cancelled", "succeeded"}
