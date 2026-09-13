"""Process supervision: deadlines and process-tree cleanup.

Style note.  v1's versions of these tests asserted that a bash child had written
a pid file inside a 250 ms window.  On a loaded host bash sometimes took longer
to start, so the safety tests failed for reasons that had nothing to do with
the safety code -- and a flaky safety test gets ignored.  Here the child
*reports its own grandchild pid on stdout*, so the assertion never races the
scheduler: by the time the line arrives, the grandchild provably exists.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from openroad_platform_runtime.guardian import ProcessGuardian

pytestmark = pytest.mark.skipif(
    os.name != "posix" or not Path("/proc").is_dir(),
    reason="process-tree cleanup is Linux-specific",
)


def alive(pid: int) -> bool:
    return ProcessGuardian._process_is_running(pid)


def wait_gone(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return not alive(pid)


def test_timeout_is_enforced_even_when_the_child_is_silent(tmp_path: Path):
    guardian = ProcessGuardian(poll_interval=0.02, terminate_grace=1.0)
    started = time.monotonic()
    outcome = guardian.run(
        ["sleep", "60"],
        log_path=tmp_path / "silent.log",
        timeout_seconds=0.5,
    )
    elapsed = time.monotonic() - started

    assert outcome.timed_out is True
    # Generous: this asserts the deadline is enforced, not how fast the box is.
    assert elapsed < 15, f"timeout took {elapsed:.2f}s"
    assert "wall-clock timeout" in (tmp_path / "silent.log").read_text(encoding="utf-8")


def test_a_noisy_child_cannot_starve_its_own_deadline(tmp_path: Path):
    """A process that never stops printing must still be killed on time."""
    guardian = ProcessGuardian(poll_interval=0.01, terminate_grace=1.0)
    noisy = (
        "import sys\n"
        "while True:\n"
        "    sys.stdout.write('noise ' * 20 + '\\n')\n"
    )
    outcome = guardian.run(
        [sys.executable, "-u", "-c", noisy],
        log_path=tmp_path / "noisy.log",
        timeout_seconds=0.5,
    )
    assert outcome.timed_out is True


def test_the_whole_descendant_tree_is_killed(tmp_path: Path):
    """A child that starts a background process must not leave it running.

    The child prints its own grandchild's pid, so the test learns the pid from
    the process itself rather than from a racy side-channel file.
    """
    guardian = ProcessGuardian(poll_interval=0.02, terminate_grace=1.0)
    grandchild_pids: list[int] = []

    outcome = guardian.run(
        ["bash", "-c", "sleep 60 & echo PID:$!; wait"],
        log_path=tmp_path / "tree.log",
        timeout_seconds=1.0,
        on_line=lambda line: grandchild_pids.append(int(line.split(":")[1]))
        if line.startswith("PID:") else None,
    )

    assert outcome.timed_out is True
    assert grandchild_pids, "the child never reported its grandchild pid"
    assert wait_gone(grandchild_pids[0]), (
        f"grandchild {grandchild_pids[0]} survived the timeout"
    )


def test_a_child_that_detaches_with_setsid_is_still_killed(tmp_path: Path):
    """setsid() is not an escape hatch from the platform's cleanup."""
    guardian = ProcessGuardian(poll_interval=0.02, terminate_grace=1.0)
    grandchild_pids: list[int] = []

    outcome = guardian.run(
        ["bash", "-c", "setsid sleep 60 & echo PID:$!; wait"],
        log_path=tmp_path / "detached.log",
        timeout_seconds=1.0,
        on_line=lambda line: grandchild_pids.append(int(line.split(":")[1]))
        if line.startswith("PID:") else None,
    )

    assert outcome.timed_out is True
    assert grandchild_pids
    assert wait_gone(grandchild_pids[0]), (
        f"detached grandchild {grandchild_pids[0]} survived the timeout"
    )


def test_cancellation_works_without_any_process_output(tmp_path: Path):
    guardian = ProcessGuardian(poll_interval=0.02, terminate_grace=1.0)
    started = time.monotonic()
    outcome = guardian.run(
        ["sleep", "60"],
        log_path=tmp_path / "cancel.log",
        timeout_seconds=60,
        cancel_requested=lambda: time.monotonic() - started > 0.3,
    )
    assert outcome.cancelled is True
    assert outcome.timed_out is False
    assert "cancellation requested" in (
        tmp_path / "cancel.log"
    ).read_text(encoding="utf-8")


def test_output_is_captured_and_streamed(tmp_path: Path):
    guardian = ProcessGuardian(poll_interval=0.02, terminate_grace=1.0)
    seen: list[str] = []
    outcome = guardian.run(
        ["bash", "-c", "echo first; echo second"],
        log_path=tmp_path / "out.log",
        timeout_seconds=10,
        on_line=seen.append,
    )
    assert outcome.returncode == 0
    assert outcome.timed_out is False
    log = (tmp_path / "out.log").read_text(encoding="utf-8")
    assert "first" in log and "second" in log
    assert any("first" in line for line in seen)


def test_a_failing_observer_does_not_destroy_the_run(tmp_path: Path):
    """Telemetry is not authority.

    If a progress observer raises (a locked event store, a disconnected UI),
    the tool's own result must survive.  Turning that into a failed experiment
    would lose real EDA work to a display bug.
    """
    guardian = ProcessGuardian(poll_interval=0.02, terminate_grace=1.0)

    def exploding_observer(line: str) -> None:
        raise RuntimeError("observer is broken")

    outcome = guardian.run(
        ["bash", "-c", "echo data"],
        log_path=tmp_path / "obs.log",
        timeout_seconds=10,
        on_line=exploding_observer,
    )
    assert outcome.returncode == 0
    assert outcome.timed_out is False
    log = (tmp_path / "obs.log").read_text(encoding="utf-8")
    assert "data" in log
    assert "observer failed" in log


def test_zero_or_negative_timeout_is_refused(tmp_path: Path):
    guardian = ProcessGuardian()
    with pytest.raises(ValueError, match="timeout_seconds"):
        guardian.run(["true"], log_path=tmp_path / "x.log", timeout_seconds=0)


def test_invalid_construction_is_refused():
    with pytest.raises(ValueError, match="poll_interval"):
        ProcessGuardian(poll_interval=0)
    with pytest.raises(ValueError, match="terminate_grace"):
        ProcessGuardian(terminate_grace=-1)
