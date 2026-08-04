from __future__ import annotations

import os
import time

from openroad_platform_execution.process_guardian import ProcessGuardian


def _process_is_running(pid: int) -> bool:
    stat = f"/proc/{pid}/stat"
    try:
        state = open(stat, encoding="utf-8").read().split()[2]
    except FileNotFoundError:
        return False
    return state != "Z"


def test_timeout_applies_to_silent_process_and_kills_process_group(tmp_path):
    pid_file = tmp_path / "child.pid"
    script = f"sleep 30 & child=$!; printf '%s' $child > {pid_file}; wait"
    guardian = ProcessGuardian(poll_interval=0.02, terminate_grace=0.1)
    started = time.monotonic()
    outcome = guardian.run(
        ["bash", "-c", script],
        log_path=tmp_path / "silent.log",
        timeout_seconds=0.25,
    )
    elapsed = time.monotonic() - started

    assert outcome.timed_out is True
    assert elapsed < 2
    child_pid = int(pid_file.read_text())
    for _ in range(50):
        if not _process_is_running(child_pid):
            break
        time.sleep(0.02)
    assert not _process_is_running(child_pid)
    assert "wall-clock timeout" in (tmp_path / "silent.log").read_text()


def test_cancellation_is_checked_without_process_output(tmp_path):
    started = time.monotonic()
    guardian = ProcessGuardian(poll_interval=0.02, terminate_grace=0.1)
    outcome = guardian.run(
        ["sleep", "30"],
        log_path=tmp_path / "cancel.log",
        timeout_seconds=10,
        cancel_requested=lambda: time.monotonic() - started > 0.15,
    )
    assert outcome.cancelled is True
    assert outcome.timed_out is False
    assert time.monotonic() - started < 2

