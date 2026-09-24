"""Declared resource bounds: what is refused, and what a breach becomes.

The enforcement itself is Linux-only and lives in ``test_guardian.py``.  What is
tested here runs anywhere, because the two decisions that matter most are not
about measuring a process tree at all:

* a bound the host cannot measure is **refused at submission**, not accepted and
  quietly ignored, and
* a breach becomes a named failure rather than a bare exit code.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest
from openroad_platform_contracts import (
    ContractError,
    PluginManifest,
    ResourceRequest,
    RuntimeStatus,
    TaskSpec,
)
from openroad_platform_runtime import (
    ResourceLimitsUnsupported,
    RuntimeStore,
    WorkflowRuntime,
)
from openroad_platform_runtime.adapter import ProcessAdapter
from openroad_platform_runtime.guardian import ProcessGuardian, ProcessOutcome


class Resolver:
    def __init__(self, manifest: PluginManifest):
        self.manifest = manifest

    def resolve(self, plugin_id, *, version=None, capability=None, arch=None):
        if plugin_id != self.manifest.plugin_id:
            raise LookupError(plugin_id)
        return self.manifest


def manifest() -> PluginManifest:
    return PluginManifest(
        plugin_id="fake-capability", plugin_version="1.0.0",
        adapter_entry=(sys.executable, "-c", "print()"),
        capabilities=("do.thing",), supported_arch=("aarch64", "x86_64"),
        default_timeout_seconds=60,
    )


def runtime(tmp_path: Path) -> WorkflowRuntime:
    return WorkflowRuntime(
        RuntimeStore(tmp_path / "runtime.db"), Resolver(manifest()),
        workspace_root=tmp_path / "ws", worker_id="test-worker",
    )


# --------------------------------------------------------------------------
# the contract
# --------------------------------------------------------------------------

def test_an_empty_request_declares_nothing():
    """``None`` per field means "not bounded", which is not "bounded at zero".

    The distinction is the whole reason the fields are optional rather than
    defaulted: a default of 0 would read as "this experiment may use no CPU".
    """
    assert ResourceRequest().declared is False
    assert ResourceRequest(cpu_seconds=1).declared is True


@pytest.mark.parametrize("payload", [
    {"cpu_seconds": 0}, {"cpu_seconds": -1}, {"cpu_seconds": True},
    {"memory_bytes": 0}, {"memory_bytes": -1}, {"memory_bytes": 1.5},
    {"processes": 0}, {"processes": -3},
])
def test_a_bound_must_be_a_positive_number(payload):
    with pytest.raises(ContractError):
        ResourceRequest.from_dict(payload).validate()


def test_a_bound_must_be_positive_even_when_built_directly():
    with pytest.raises(ContractError, match="positive"):
        TaskSpec(
            task_id="t", project_id="p", design_id="d", plugin_id="x",
            resources=ResourceRequest(memory_bytes=0),
        ).validate()


def test_a_task_without_resources_still_loads():
    """The field is optional, so every task written before it is still valid."""
    payload = {
        "schema_version": 3, "task_id": "t", "project_id": "p",
        "design_id": "d", "plugin_id": "x",
    }
    assert TaskSpec.from_dict(payload).resources is None


def test_a_resource_request_round_trips():
    spec = TaskSpec(
        task_id="t", project_id="p", design_id="d", plugin_id="x",
        resources=ResourceRequest(cpu_seconds=90, memory_bytes=1 << 30,
                                  processes=16),
    )
    assert TaskSpec.from_dict(spec.to_dict()) == spec


def test_effective_reservation_preserves_runtime_limits(tmp_path):
    """Admission defaults must not erase caller limits enforced by guardian."""
    rt = runtime(tmp_path)
    spec = TaskSpec(
        task_id="task-effective", project_id="p", design_id="d",
        plugin_id="fake-capability",
        resources=ResourceRequest(cpu_seconds=90, processes=4),
    )

    effective = rt.config.reservation_for(spec)

    assert effective.cpu_seconds == 90
    assert effective.processes == 4
    assert effective.cpu_cores == 1
    assert effective.memory_bytes == 6 << 30


def test_memory_above_default_requires_manual_approval(tmp_path):
    rt = runtime(tmp_path)
    spec = TaskSpec(
        task_id="task-large-memory", project_id="p", design_id="d",
        plugin_id="fake-capability",
        resources=ResourceRequest(memory_bytes=8 << 30),
    )
    with pytest.raises(ValueError, match="memory_approval"):
        rt.submit(spec)
    approved = replace(spec, labels={"memory_approval": "manual"})
    assert rt.submit(approved).status is RuntimeStatus.QUEUED


# --------------------------------------------------------------------------
# what a breach becomes
# --------------------------------------------------------------------------

def test_a_breached_limit_is_a_named_failure_not_an_exit_code(tmp_path):
    """The platform decided this outcome, so the result file is not consulted.

    A plugin that ignored its bounds does not get to report on them, and the
    failure names the measurement and the request -- "exit code 137" would send
    an operator looking for a crash that did not happen.
    """
    outcome = ProcessOutcome(
        command=("whatever",), returncode=-9, seconds=3.0,
        exceeded="memory_bytes: the tree held 900 bytes resident, above the "
                 "requested 100",
    )
    result = ProcessAdapter._load_result(
        tmp_path / "never-written.json", outcome, manifest(),
        started_at="t0", ended_at="t1",
    )
    assert result.status is RuntimeStatus.FAILED
    assert result.failure is not None
    assert result.failure["category"] == "resource_exceeded"
    assert "above the requested 100" in result.failure["message"]
    assert result.failure["retryable"] is False


def test_a_limit_this_host_cannot_measure_is_refused_at_submission(tmp_path):
    """Not applied-and-forgotten: the caller is told before a run exists.

    Which host this is depends on the machine, so the test asserts whichever
    answer is true here rather than pretending to be Linux.
    """
    rt = runtime(tmp_path)
    spec = TaskSpec(
        task_id="task-limited", project_id="p", design_id="d",
        plugin_id="fake-capability",
        resources=ResourceRequest(memory_bytes=1 << 20),
    )
    if ProcessGuardian.supports_limits():
        pytest.skip("this host can measure a process tree; the refusal is for one that cannot")
    with pytest.raises(ResourceLimitsUnsupported, match="cannot be enforced"):
        rt.submit(spec)
    assert rt.store.find_run_by_task_id("task-limited") is None


def test_a_task_with_no_limits_is_never_refused_on_that_ground(tmp_path):
    """The common case must not depend on where the platform runs."""
    rt = runtime(tmp_path)
    run = rt.submit(TaskSpec(
        task_id="task-plain", project_id="p", design_id="d",
        plugin_id="fake-capability",
    ))
    assert run.status.value == "queued"
