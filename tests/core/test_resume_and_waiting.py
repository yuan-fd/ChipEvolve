"""Two answers a queue owes the person waiting in it.

``queued`` looks identical whether the machine is full, whether nothing is
running, or whether something is stuck, and those need three different
responses.  And a worker that dies should cost a lease, not five hours.
"""

from __future__ import annotations

import sys
from pathlib import Path

from openroad_platform_contracts import (
    AttemptStatus,
    PluginManifest,
    RuntimeRequirements,
    RuntimeStatus,
    TaskSpec,
)
from openroad_platform_runtime import RuntimeStore, WorkflowRuntime
from openroad_platform_runtime.adapter import ProcessAdapter
from openroad_platform_runtime.guardian import ProcessGuardian
from openroad_platform_runtime.resource_query import ResourceQuery

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_adapter.py"


class Resolver:
    def __init__(self, manifest: PluginManifest):
        self.manifest = manifest

    def resolve(self, plugin_id, *, version=None, capability=None, arch=None):
        if plugin_id != self.manifest.plugin_id:
            raise LookupError(plugin_id)
        return self.manifest


def manifest(resumable: bool = False) -> PluginManifest:
    return PluginManifest(
        plugin_id="fake-capability", plugin_version="1.0.0",
        adapter_entry=(sys.executable, str(FIXTURE)),
        capabilities=("do.thing",), supported_arch=("aarch64", "x86_64"),
        artifact_rules=({"kind": "report", "required": True},),
        default_timeout_seconds=60,
        requirements=RuntimeRequirements(resumable=resumable),
    )


def runtime(tmp_path: Path, *, resumable: bool = False,
            capacity_cpu: int = 4, capacity_memory: int = 16 << 30
            ) -> tuple[WorkflowRuntime, ResourceQuery]:
    store = RuntimeStore(tmp_path / "runtime.db")
    from openroad_platform_runtime import RuntimeConfig

    rt = WorkflowRuntime(
        store, Resolver(manifest(resumable)),
        adapter=ProcessAdapter(ProcessGuardian(poll_interval=0.02,
                                               terminate_grace=1.0)),
        config=RuntimeConfig(
            workspace_root=tmp_path / "ws", worker_id="test-worker",
            capacity_cpu_cores=capacity_cpu,
            capacity_memory_bytes=capacity_memory,
        ),
    )
    return rt, ResourceQuery(store, rt.config)


def task(name: str, **overrides) -> TaskSpec:
    base = dict(
        task_id=f"task-{name}", project_id="p", design_id="d",
        plugin_id="fake-capability", inputs={"behaviour": "ok"},
        timeout_seconds=30,
    )
    base.update(overrides)
    return TaskSpec(**base)


def start_and_hold(rt: WorkflowRuntime, run_id: str):
    """Claim an attempt against the store, leaving it running.  Returns it."""
    record = rt.store.get_run(run_id)
    stage = rt.store.list_stages(run_id)[0]
    workspace = (rt.config.workspace_root / run_id / stage.stage_run_id
                 / "attempt-1")
    workspace.mkdir(parents=True, exist_ok=True)
    attempt = rt.store.start_attempt(
        stage.stage_run_id, worker_id="test-worker", workspace=workspace,
        lease_seconds=30,
        resources=rt.config.reservation_for(record.task_spec),
        capacity_cpu_cores=rt.config.capacity_cpu_cores,
        capacity_memory_bytes=rt.config.capacity_memory_bytes,
        platform_fraction=rt.config.platform_fraction,
    )
    assert attempt is not None, "the attempt should have been claimable"
    return attempt


def start_and_lose(rt: WorkflowRuntime, run_id: str) -> str:
    """Claim an attempt, then let its lease expire.  Returns the attempt id.

    The claim is made against the store rather than by running anything: an
    attempt driven to completion has no lease left to lose, and the lease is
    the thing under test.
    """
    attempt = start_and_hold(rt, run_id)
    # Push the lease into the past rather than waiting for it.
    rt.store._connection.execute(
        "UPDATE runtime_attempts SET lease_expires_at = ? WHERE attempt_id = ?",
        ("2000-01-01T00:00:00+00:00", attempt.attempt_id),
    )
    rt.store.reclaim_expired_attempts()
    return attempt.attempt_id


# --------------------------------------------------------------------------
# why a run has not started
# --------------------------------------------------------------------------

def test_a_queued_run_with_room_says_so(tmp_path):
    rt, resources = runtime(tmp_path)
    run = rt.submit(task("roomy"))
    assert "claimable" in resources.waiting_for(run.run_id)
    assert "waiting for a worker" in resources.waiting_for(run.run_id)


def test_a_queued_run_without_room_names_the_shortfall(tmp_path):
    """The number is the point: "queued" is not something anyone can act on.

    Driven with default reservations rather than declared ones, so the test
    says the same thing on a host that can measure a process tree and on one
    that cannot -- declaring a bound is refused where it could not be enforced,
    which is a different rule and is tested elsewhere.
    """
    rt, resources = runtime(tmp_path, capacity_cpu=1, capacity_memory=16 << 30)
    # The budget is 1 core, so one default reservation fills it.
    first = rt.submit(task("hog"))
    start_and_hold(rt, first.run_id)
    assert rt.store.resource_totals()[0] == 1

    second = rt.submit(task("blocked"))
    reason = resources.waiting_for(second.run_id)
    assert reason is not None and reason.startswith("resource:")
    assert "reserves 1 cores and 6442450944 bytes" in reason
    assert "0 cores and " in reason


def test_a_running_run_is_not_waiting_for_anything(tmp_path):
    rt, resources = runtime(tmp_path)
    run = rt.submit(task("moving"))
    rt.execute_once(run.run_id)
    assert resources.waiting_for(run.run_id) is None


def test_the_explanation_uses_the_same_numbers_as_the_reservation(tmp_path):
    """A shortfall explained with different numbers than the reservation is a
    wild goose chase.  Both come from ``RuntimeConfig.reservation_for``."""
    rt, resources = runtime(tmp_path, capacity_cpu=4, capacity_memory=16 << 30)
    run = rt.submit(task("defaulted"))
    reserved = rt.config.reservation_for(rt.store.get_run(run.run_id).task_spec)
    assert reserved.cpu_cores == 1
    assert reserved.memory_bytes == 6 << 30
    # With room to spare the answer is the other one; the reservation is what
    # decides, and it is the same object the runtime will use.
    assert resources.waiting_for(run.run_id).startswith("claimable")


def test_the_resource_view_reports_what_would_be_reserved(tmp_path):
    rt, resources = runtime(tmp_path)
    run = rt.submit(task("view"))
    view = resources.run(run.run_id)
    assert view["would_reserve"]["cpu_cores"] == 1
    assert view["waiting_for"].startswith("claimable")


# --------------------------------------------------------------------------
# a lost lease costs a lease, not the work
# --------------------------------------------------------------------------

def test_a_resumable_capability_gets_its_run_back(tmp_path):
    rt, _ = runtime(tmp_path, resumable=True)
    run = rt.submit(task("resume", max_attempts=2))
    attempt_id = start_and_lose(rt, run.run_id)

    assert rt.store.get_run(run.run_id).status is RuntimeStatus.RETRY_WAIT
    assert rt.store.list_attempts(
        rt.store.list_stages(run.run_id)[0].stage_run_id
    )[0].status is AttemptStatus.LOST
    # And it is claimable again.
    assert run.run_id in rt.store.runnable_runs()
    assert attempt_id


def test_a_capability_that_cannot_resume_stops_instead(tmp_path):
    """Restarting from nothing is not resuming, and guessing costs five hours."""
    rt, _ = runtime(tmp_path, resumable=False)
    run = rt.submit(task("no-resume", max_attempts=5))
    start_and_lose(rt, run.run_id)
    assert rt.store.get_run(run.run_id).status is RuntimeStatus.LOST


def test_the_attempt_budget_still_bounds_resuming(tmp_path):
    """A lost lease is an attempt: the host spent that time.

    Without this, a machine that loses its worker every time would loop for
    ever, which is a worse failure than stopping.
    """
    rt, _ = runtime(tmp_path, resumable=True)
    run = rt.submit(task("once", max_attempts=1))
    start_and_lose(rt, run.run_id)
    assert rt.store.get_run(run.run_id).status is RuntimeStatus.LOST


def test_a_resumed_attempt_continues_in_the_workspace_it_had(tmp_path):
    """The whole point: a flow that resumes by re-running its makefile needs
    the results it already has, so a fresh directory would be the opposite."""
    rt, _ = runtime(tmp_path, resumable=True)
    run = rt.submit(task("same-dir", max_attempts=2))
    start_and_lose(rt, run.run_id)

    stage = rt.store.list_stages(run.run_id)[0]
    first = rt.store.list_attempts(stage.stage_run_id)[0]
    marker = Path(first.workspace) / "halfway.txt"
    marker.write_text("the flow got this far", encoding="utf-8")

    rt.execute_once(run.run_id)
    second = rt.store.list_attempts(stage.stage_run_id)[1]
    assert second.workspace == first.workspace
    assert marker.read_text(encoding="utf-8") == "the flow got this far"


def test_a_fresh_attempt_gets_a_fresh_workspace_when_not_resuming(tmp_path):
    rt, _ = runtime(tmp_path, resumable=False)
    run = rt.submit(task("fresh", max_attempts=1))
    rt.execute_once(run.run_id)
    stage = rt.store.list_stages(run.run_id)[0]
    first = rt.store.list_attempts(stage.stage_run_id)[0]
    assert Path(first.workspace).name == "attempt-1"
    assert Path(first.workspace).is_dir()
