"""The worker that makes runs progress without an operator.

The assertions worth having are about what a cycle does in the awkward cases: a
worker that died holding a lease, a cancellation nobody could observe, a run
that cannot be advanced, and two workers racing for the same stage.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from openroad_platform_contracts import (
    AttemptStatus,
    PluginManifest,
    RuntimeStatus,
    TaskSpec,
)
from openroad_platform_runtime import (
    CycleReport,
    ProcessAdapter,
    RuntimeConfig,
    RuntimeStore,
    RuntimeWorker,
    WorkflowRuntime,
)
from openroad_platform_runtime.guardian import ProcessGuardian

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "fake_adapter.py"


def manifest(**overrides) -> PluginManifest:
    base = dict(
        plugin_id="fake-capability", plugin_version="1.0.0",
        adapter_entry=(sys.executable, str(FIXTURE)),
        capabilities=("do.thing",),
        supported_arch=("aarch64", "x86_64", "arm64"),
        input_schema={}, output_schema={},
        artifact_rules=(
            {"kind": "report", "required": True},
            {"kind": "log", "required": False},
        ),
        default_timeout_seconds=60,
    )
    base.update(overrides)
    return PluginManifest(**base)


class Resolver:
    def __init__(self, m: PluginManifest):
        self.manifest = m

    def resolve(self, plugin_id, *, version=None, capability=None, arch=None):
        if plugin_id != self.manifest.plugin_id:
            raise LookupError(plugin_id)
        return self.manifest


def task(task_id: str = "t-1", behaviour: str = "ok") -> TaskSpec:
    return TaskSpec(
        task_id=task_id, project_id="p", design_id="d",
        plugin_id="fake-capability", inputs={"behaviour": behaviour},
        timeout_seconds=30,
    )


@pytest.fixture()
def worker(tmp_path: Path):
    store = RuntimeStore(tmp_path / "runtime.db")
    runtime = WorkflowRuntime(
        store, Resolver(manifest()),
        config=RuntimeConfig(workspace_root=tmp_path / "ws", worker_id="w1"),
        adapter=ProcessAdapter(ProcessGuardian(poll_interval=0.02,
                                               terminate_grace=1.0)),
    )
    yield RuntimeWorker(store, runtime, idle_seconds=0.01), store, runtime
    store.close()


# --------------------------------------------------------------------------
# advancing work
# --------------------------------------------------------------------------

def test_a_cycle_advances_a_submitted_run(worker):
    w, store, runtime = worker
    run = runtime.submit(task())
    report = w.cycle()
    assert report.advanced == 1
    assert store.get_run(run.run_id).status is RuntimeStatus.SUCCEEDED


def test_a_cycle_with_nothing_to_do_reports_no_work(worker):
    w, _, _ = worker
    report = w.cycle()
    assert report.to_dict() == {"reclaimed": 0, "cancelled": 0,
                                "advanced": 0, "failed": 0}
    assert report.did_work is False


def test_several_runs_are_advanced_in_one_cycle(worker):
    w, store, runtime = worker
    runs = [runtime.submit(task(f"t-{i}")) for i in range(3)]
    report = w.cycle()
    assert report.advanced == 3
    assert all(store.get_run(r.run_id).status is RuntimeStatus.SUCCEEDED
               for r in runs)


def test_a_cycle_is_bounded_by_the_batch(worker):
    w, store, runtime = worker
    w.batch = 2
    for i in range(3):
        runtime.submit(task(f"t-{i}"))
    assert w.cycle().advanced == 2
    # The remainder is picked up next cycle rather than lost.
    assert w.cycle().advanced == 1


def test_a_failing_run_is_advanced_and_recorded_as_failed(worker):
    w, store, runtime = worker
    run = runtime.submit(task(behaviour="fail"))
    report = w.cycle()
    assert report.advanced == 1
    assert store.get_run(run.run_id).status is RuntimeStatus.FAILED


def test_a_run_that_cannot_be_advanced_does_not_stop_the_cycle(worker):
    """One broken run must not block every healthy one behind it."""
    w, store, runtime = worker
    healthy = runtime.submit(task("healthy"))
    # A task for a plugin the resolver refuses: submitting bypasses resolution,
    # so the failure lands inside the cycle.
    broken = store.submit_run(
        TaskSpec(task_id="broken", project_id="p", design_id="d",
                 plugin_id="absent-plugin"),
        stage_key="main", plugin_version="1.0.0",
    )
    report = w.cycle()
    assert report.failed == 1
    assert store.get_run(healthy.run_id).status is RuntimeStatus.SUCCEEDED


# --------------------------------------------------------------------------
# reclaiming leases
# --------------------------------------------------------------------------

def test_a_cycle_reclaims_an_expired_lease(worker):
    """A worker that died mid-attempt left a row saying running."""
    w, store, runtime = worker
    run = runtime.submit(task())
    stage = store.list_stages(run.run_id)[0]
    attempt = store.start_attempt(stage.stage_run_id, worker_id="dead",
                                  workspace=Path("/tmp/nowhere"),
                                  lease_seconds=30)
    # Age the lease rather than waiting for it.
    with store._lock:  # noqa: SLF001
        store._connection.execute(  # noqa: SLF001
            "UPDATE runtime_attempts SET lease_expires_at = ?",
            ("2000-01-01T00:00:00+00:00",),
        )
    report = w.cycle()
    assert report.reclaimed == 1
    attempts = store.list_attempts(stage.stage_run_id)
    assert attempts[0].status is AttemptStatus.LOST
    assert attempts[0].attempt_id == attempt.attempt_id


def test_a_reclaimed_run_settles_in_the_same_cycle(worker):
    """Reclamation and settlement happen together.

    Marking only the attempt would leave the run in ``running`` with no
    attempt that can ever finish it: a run nobody would ever see fail.  The
    cycle must settle it rather than requiring a second pass.
    """
    w, store, runtime = worker
    run = runtime.submit(task("orphan"))
    stage = store.list_stages(run.run_id)[0]
    store.start_attempt(stage.stage_run_id, worker_id="dead",
                        workspace=Path("/tmp/nowhere"), lease_seconds=30)
    with store._lock:  # noqa: SLF001
        store._connection.execute(  # noqa: SLF001
            "UPDATE runtime_attempts SET lease_expires_at = ?",
            ("2000-01-01T00:00:00+00:00",),
        )
    report = w.cycle()
    assert report.reclaimed == 1
    settled = store.get_run(run.run_id)
    assert settled.status is RuntimeStatus.LOST
    assert settled.terminal_reason == "lease_expired"
    # And it is not advanced afterwards: LOST is terminal.
    assert w.cycle().advanced == 0


# --------------------------------------------------------------------------
# cancellations nobody could observe
# --------------------------------------------------------------------------

def test_a_run_cancelled_before_it_started_is_settled(worker):
    """A queued run has no running attempt to notice the request.

    Without this the request would sit in cancel_requested forever and the
    caller would never see the run reach a terminal state.
    """
    w, store, runtime = worker
    run = runtime.submit(task())
    store.request_cancel(run.run_id)
    assert store.get_run(run.run_id).status is RuntimeStatus.CANCEL_REQUESTED

    report = w.cycle()
    assert report.cancelled == 1
    settled = store.get_run(run.run_id)
    assert settled.status is RuntimeStatus.CANCELLED
    assert "before the attempt started" in (settled.terminal_reason or "")


def test_a_cancellation_with_a_live_attempt_is_left_to_the_attempt(worker):
    """The running attempt owns the transition; the worker must not pre-empt it
    and declare a cancellation while the process is still going."""
    w, store, runtime = worker
    run = runtime.submit(task("busy", behaviour="noisy"))
    stage = store.list_stages(run.run_id)[0]
    store.start_attempt(stage.stage_run_id, worker_id="w-live",
                        workspace=Path("/tmp/live"), lease_seconds=30)
    store.request_cancel(run.run_id)

    assert store.abandoned_cancellations() == []
    assert w.cycle().cancelled == 0


def test_a_settled_cancellation_is_not_settled_twice(worker):
    w, store, runtime = worker
    run = runtime.submit(task())
    store.request_cancel(run.run_id)
    assert w.cycle().cancelled == 1
    assert w.cycle().cancelled == 0


# --------------------------------------------------------------------------
# concurrency
# --------------------------------------------------------------------------

def test_two_workers_do_not_execute_the_same_stage(tmp_path):
    """The lease is what stops two workers running the same experiment."""
    path = tmp_path / "runtime.db"
    store_a = RuntimeStore(path)
    store_b = RuntimeStore(path)
    try:
        def build(store, worker_id):
            runtime = WorkflowRuntime(
                store, Resolver(manifest()),
                config=RuntimeConfig(workspace_root=tmp_path / f"ws-{worker_id}",
                                     worker_id=worker_id),
                adapter=ProcessAdapter(),
            )
            return RuntimeWorker(store, runtime, idle_seconds=0.01)

        a, b = build(store_a, "a"), build(store_b, "b")
        runtime_a = a.runtime
        run = runtime_a.submit(task())

        report_a = CycleReport()
        report_b = CycleReport()
        barrier = threading.Barrier(2)

        def go(worker, report):
            barrier.wait()
            report.__init__(**worker.cycle().to_dict())  # noqa: SLF001

        threads = [threading.Thread(target=go, args=(a, report_a)),
                   threading.Thread(target=go, args=(b, report_b))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one worker advanced it; the other found the lease taken.
        assert report_a.advanced + report_b.advanced == 1
        assert store_a.get_run(run.run_id).status is RuntimeStatus.SUCCEEDED
    finally:
        store_a.close()
        store_b.close()


# --------------------------------------------------------------------------
# serving
# --------------------------------------------------------------------------

def test_serve_forever_stops_when_asked(worker):
    w, store, runtime = worker
    run = runtime.submit(task())
    seen: list[CycleReport] = []
    w.on_cycle = seen.append
    stop = threading.Event()

    def stopper():
        # Let it do at least one useful cycle, then stop.
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if store.get_run(run.run_id).status is RuntimeStatus.SUCCEEDED:
                break
            time.sleep(0.02)
        stop.set()

    thread = threading.Thread(target=stopper)
    thread.start()
    w.serve_forever(stop)
    thread.join()

    assert store.get_run(run.run_id).status is RuntimeStatus.SUCCEEDED
    assert seen, "the worker never reported a cycle"


def test_an_invalid_configuration_is_refused(worker):
    _, store, runtime = worker
    with pytest.raises(ValueError, match="idle_seconds"):
        RuntimeWorker(store, runtime, idle_seconds=-1)
    with pytest.raises(ValueError, match="batch"):
        RuntimeWorker(store, runtime, batch=0)


def test_the_store_bounds_the_query_limit(worker):
    _, store, _ = worker
    with pytest.raises(Exception, match="limit must be between"):
        store.runnable_runs(limit=0)
    with pytest.raises(Exception, match="limit must be between"):
        store.abandoned_cancellations(limit=10_000)


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
        [sys.executable, "-m", "openroad_platform_runtime.worker",
         "--state-root", str(state), "--plugins-root", str(plugins),
         "--once", "--quiet"],
        capture_output=True, text=True, timeout=60,
        env={**__import__("os").environ,
             "PYTHONPATH": __import__("os").pathsep.join([
                 str(REPO_ROOT / "contracts" / "src"),
                 str(REPO_ROOT / "core" / "runtime" / "src"),
                 str(REPO_ROOT / "core" / "registry" / "src"),
                 str(REPO_ROOT / "core" / "evaluator" / "src"),
             ])},
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout.strip().splitlines()[-1])
    assert report == {"reclaimed": 0, "cancelled": 0, "advanced": 0, "failed": 0}
    # The cycle created the store it was pointed at.
    assert (state / "runtime.db").is_file()
