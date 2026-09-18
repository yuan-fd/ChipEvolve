"""Process supervision: deadlines and process-tree cleanup.

Style note.  v1's versions of these tests asserted that a bash child had written
a pid file inside a 250 ms window.  On a loaded host bash sometimes took longer
to start, so the safety tests failed for reasons that had nothing to do with
the safety code -- and a flaky safety test gets ignored.

The first version of these tests replaced the pid file with a pid on stdout,
which removed one race and left another: the *deadline* still had to outlast
process startup.  Measured on the build host at load average 74,
``bash -c "sleep 0.1 & echo PID:$!; wait"`` took up to **13.9 seconds** to
complete.  Any deadline short enough to make a fast test is therefore also short
enough to fire before the child has forked anything, and the assertion then
describes a process tree that never existed.

The tree-cleanup tests are consequently driven by a **handshake**: the child
reports its own grandchild pid, and the moment that line arrives the test asks
for the run to stop.  The stop path is the same code for a cancellation and for
a deadline -- ``_terminate_tree`` -- so the property under test is unchanged,
and the test no longer depends on how fast the host schedules a new process.
The deadline itself is covered separately, by a test that needs only a silent
child and no timing assumption about startup.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest
from openroad_platform_contracts import ResourceRequest
from openroad_platform_runtime.guardian import (
    TELEMETRY_QUEUE_SIZE,
    ProcessGuardian,
)

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


def test_raw_log_keeps_the_tail_when_telemetry_is_noisy(tmp_path: Path):
    """Raw evidence is complete even when progress delivery is bounded."""
    lines = TELEMETRY_QUEUE_SIZE * 8
    script = (
        "import sys, time\n"
        f"for index in range({lines}): print(f'line-{{index}}')\n"
        "print('TAIL-MARKER', flush=True)\n"
        "time.sleep(60)\n"
    )
    seen: list[str] = []
    guardian = ProcessGuardian(poll_interval=0.01, terminate_grace=1.0)

    outcome = guardian.run(
        [sys.executable, "-u", "-c", script],
        log_path=tmp_path / "noisy-tail.log",
        timeout_seconds=1.0,
        on_line=seen.append,
    )

    raw_log = (tmp_path / "noisy-tail.log").read_text(encoding="utf-8")
    assert outcome.timed_out is True
    assert "TAIL-MARKER" in raw_log
    assert len(seen) < lines


def stop_once_the_tree_is_visible(guardian: ProcessGuardian, script: str,
                                  log_path: Path):
    """Run *script* and stop it as soon as it reports its grandchild's pid.

    Returns (outcome, pids).  The stop is a handshake, not a deadline: the test
    refuses to assert about a tree until the child has told it the tree exists.
    """
    pids: list[int] = []
    stop = threading.Event()

    def on_line(line: str) -> None:
        if line.startswith("PID:"):
            pids.append(int(line.split(":", 1)[1]))
            stop.set()

    outcome = guardian.run(
        ["bash", "-c", script],
        log_path=log_path,
        # Generous: it only has to outlast the handshake, and the handshake ends
        # the run as soon as the tree is visible.
        timeout_seconds=600,
        cancel_requested=stop.is_set,
        on_line=on_line,
    )
    return outcome, pids


def test_the_whole_descendant_tree_is_killed(tmp_path: Path):
    """A child that starts a background process must not leave it running.

    The child prints its own grandchild's pid, so the test learns the pid from
    the process itself rather than from a racy side-channel file -- and it waits
    for that line before stopping, rather than hoping a deadline outlasts
    startup.
    """
    guardian = ProcessGuardian(poll_interval=0.02, terminate_grace=1.0)
    outcome, pids = stop_once_the_tree_is_visible(
        guardian, "sleep 60 & echo PID:$!; wait", tmp_path / "tree.log")

    assert outcome.cancelled is True
    assert pids, "the child never reported its grandchild pid"
    assert wait_gone(pids[0]), f"grandchild {pids[0]} survived the stop"


def test_a_child_that_detaches_with_setsid_is_still_killed(tmp_path: Path):
    """setsid() is not an escape hatch from the platform's cleanup."""
    guardian = ProcessGuardian(poll_interval=0.02, terminate_grace=1.0)
    outcome, pids = stop_once_the_tree_is_visible(
        guardian, "setsid sleep 60 & echo PID:$!; wait",
        tmp_path / "detached.log")

    assert outcome.cancelled is True
    assert pids, "the child never reported its detached grandchild pid"
    assert wait_gone(pids[0]), (
        f"detached grandchild {pids[0]} survived the stop"
    )


def test_a_deadline_also_cleans_the_tree(tmp_path: Path):
    """The deadline path reaches the same cleanup, asserted without a handshake.

    A silent child is enough here: there is no tree to identify, only the
    outcome, so nothing depends on how fast the host starts a process.
    """
    guardian = ProcessGuardian(poll_interval=0.02, terminate_grace=1.0)
    outcome = guardian.run(
        ["bash", "-c", "sleep 60 & sleep 60"],
        log_path=tmp_path / "deadline.log",
        timeout_seconds=1.0,
    )
    assert outcome.timed_out is True
    assert outcome.cancelled is False


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
    # The deadline here exists only to stop a hang; it is not an assertion about
    # speed.  Measured on the build host at load 74, starting bash took up to
    # 13.9 seconds, so any deadline short enough to feel like a fast test is
    # also short enough to fire before the child has printed anything -- and the
    # failure would then look like a capture bug.
    guardian = ProcessGuardian(poll_interval=0.02, terminate_grace=1.0)
    seen: list[str] = []
    outcome = guardian.run(
        ["bash", "-c", "echo first; echo second"],
        log_path=tmp_path / "out.log",
        timeout_seconds=300,
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
        # Same reasoning as above: the deadline guards against a hang, and a
        # short one would make a capture failure look like an observer failure.
        timeout_seconds=300,
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


# --------------------------------------------------------------------------
# declared resource bounds
# --------------------------------------------------------------------------
#
# The children here are written to breach a bound and then sit still, so the
# assertion is about the *measurement* rather than about how fast the host
# schedules a new process.  Every one of them touches what it allocates: a
# reservation that is never written to holds no resident memory, and a limit on
# resident memory is exactly what is under test.

#: Spawn several children and wait, so the tree is wide rather than deep.
FORK_AND_WAIT = (
    "import subprocess, sys, time\n"
    "kids = [subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])\n"
    "        for _ in range(6)]\n"
    "print('READY', flush=True)\n"
    "time.sleep(60)\n"
)

#: Take 400 MiB and write to every page of it.
HOG_MEMORY = (
    "import time\n"
    "block = bytearray(400 * 1024 * 1024)\n"
    "for offset in range(0, len(block), 4096):\n"
    "    block[offset] = 1\n"
    "print('READY', flush=True)\n"
    "time.sleep(60)\n"
)

#: Burn CPU with one process, so the count is not what trips the limit.
SPIN = "value = 0\nwhile True:\n    value += 1\n"


def test_measure_counts_the_whole_tree(tmp_path: Path):
    """Aggregate, not per-process: the case a per-process reading would miss."""
    import subprocess

    guardian = ProcessGuardian(poll_interval=0.02, terminate_grace=1.0)
    process = subprocess.Popen(
        [sys.executable, "-c", FORK_AND_WAIT], stdout=subprocess.PIPE,
        text=True, start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 20
        processes, cpu_seconds, memory_bytes = guardian.measure(process.pid)
        while time.monotonic() < deadline and processes < 4:
            time.sleep(0.05)
            processes, cpu_seconds, memory_bytes = guardian.measure(process.pid)
        assert processes >= 4, processes
        assert memory_bytes > 0
        assert cpu_seconds >= 0
    finally:
        guardian._kill_tree(process)
        process.wait()


def test_a_declared_process_limit_is_enforced(tmp_path: Path):
    guardian = ProcessGuardian(poll_interval=0.02, terminate_grace=1.0,
                               measure_interval=0.05)
    outcome = guardian.run(
        [sys.executable, "-c", FORK_AND_WAIT],
        log_path=tmp_path / "fork.log", timeout_seconds=60,
        limits=ResourceRequest(processes=3),
    )
    assert outcome.exceeded is not None, outcome
    assert outcome.exceeded.startswith("processes:")
    assert "above the requested 3" in outcome.exceeded
    assert not outcome.timed_out


def test_a_declared_memory_limit_is_enforced(tmp_path: Path):
    guardian = ProcessGuardian(poll_interval=0.02, terminate_grace=1.0,
                               measure_interval=0.05)
    outcome = guardian.run(
        [sys.executable, "-c", HOG_MEMORY],
        log_path=tmp_path / "hog.log", timeout_seconds=60,
        limits=ResourceRequest(memory_bytes=64 * 1024 * 1024),
    )
    assert outcome.exceeded is not None, outcome
    assert outcome.exceeded.startswith("memory_bytes:")
    assert not outcome.timed_out


def test_a_declared_cpu_limit_is_enforced(tmp_path: Path):
    guardian = ProcessGuardian(poll_interval=0.02, terminate_grace=1.0,
                               measure_interval=0.05)
    outcome = guardian.run(
        [sys.executable, "-c", SPIN],
        log_path=tmp_path / "spin.log", timeout_seconds=120,
        limits=ResourceRequest(cpu_seconds=1.0),
    )
    assert outcome.exceeded is not None, outcome
    assert outcome.exceeded.startswith("cpu_seconds:")
    assert not outcome.timed_out


def test_a_bounded_run_that_stays_inside_its_bounds_is_left_alone(
    tmp_path: Path,
):
    """The check must not become a tripwire: a run inside its budget finishes."""
    guardian = ProcessGuardian(poll_interval=0.02, terminate_grace=1.0,
                               measure_interval=0.05)
    outcome = guardian.run(
        [sys.executable, "-c", "print('done')"],
        log_path=tmp_path / "fine.log", timeout_seconds=60,
        limits=ResourceRequest(cpu_seconds=30, memory_bytes=256 * 1024 * 1024,
                               processes=10),
    )
    assert outcome.exceeded is None
    assert outcome.returncode == 0
    assert not outcome.timed_out


def test_the_breach_is_written_to_the_adapter_log(tmp_path: Path):
    """An operator reads the log; a kill with no reason in it is a mystery."""
    log_path = tmp_path / "logged.log"
    guardian = ProcessGuardian(poll_interval=0.02, terminate_grace=1.0,
                               measure_interval=0.05)
    guardian.run(
        [sys.executable, "-c", SPIN],
        log_path=log_path, timeout_seconds=120,
        limits=ResourceRequest(cpu_seconds=1.0),
    )
    assert "resource limit exceeded" in log_path.read_text(encoding="utf-8")
