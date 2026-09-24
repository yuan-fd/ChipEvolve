"""Run one command under a wall-clock deadline and clean up its whole tree.

This is the platform's safety net.  An EDA job can spawn hundreds of processes;
if a timeout leaves them running, the next experiment competes with a ghost for
the same cores and its measurements are quietly wrong.

Two properties are non-negotiable and each has a test:

1. **A deadline is enforced even if the child never writes a byte.** A silent or
   enormously noisy process must not be able to outlive its budget.
2. **The whole descendant tree dies, including children that called setsid().**
   A process that deliberately detaches is still ours to clean up.
3. **A declared resource limit is enforced, or the task is refused.** A plugin
   that grows without bound takes the machine from every other experiment, so a
   bound the platform cannot measure is refused rather than quietly ignored.

On testing style: v1's timeout tests asserted that a bash child had written a
pid file within a 250 ms window.  On a loaded machine bash sometimes needed
longer to start, so the test failed for reasons unrelated to the code. A
flaky safety test is worse than no test -- people learn to ignore it. These
tests use a handshake and generous margins instead of racing the scheduler.
"""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from openroad_platform_contracts import ResourceRequest

#: How long a child gets to exit after SIGTERM before SIGKILL.
DEFAULT_TERMINATE_GRACE = 5.0

#: How often the supervisor wakes to check the deadline and cancellation.
DEFAULT_POLL_INTERVAL = 0.1

#: How often the resource meter reads /proc.  Slower than the deadline poll on
#: purpose: the deadline costs one comparison, while the meter costs two file
#: reads per process in the tree, and this module already has one regression on
#: record from walking /proc too eagerly on a busy EDA host.  Half a second is
#: far below the timescale of a flow that grows from nothing to the whole
#: machine, which is the failure this catches.
DEFAULT_MEASURE_INTERVAL = 0.5

#: Lines drained per wake-up.  Draining without a bound lets a permanently
#: non-empty queue starve deadline enforcement; a permanently noisy process
#: would then never time out.
DRAIN_BATCH = 32

#: Lines preserved from the tail once the process is gone.
FINAL_DRAIN_BATCH = 128

TELEMETRY_QUEUE_SIZE = 256


@dataclass(frozen=True)
class ProcessOutcome:
    command: tuple[str, ...]
    returncode: int
    seconds: float
    timed_out: bool = False
    cancelled: bool = False
    #: Which declared limit the tree went past, named with the measurement and
    #: the request.  ``None`` when nothing was declared or nothing was breached.
    exceeded: str | None = None
    cpu_seconds: float = 0.0
    peak_memory_bytes: int = 0
    peak_processes: int = 0


class ProcessGuardian:
    def __init__(
        self, *,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        terminate_grace: float = DEFAULT_TERMINATE_GRACE,
        measure_interval: float = DEFAULT_MEASURE_INTERVAL,
    ):
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        if terminate_grace <= 0:
            raise ValueError("terminate_grace must be positive")
        if measure_interval <= 0:
            raise ValueError("measure_interval must be positive")
        self.poll_interval = poll_interval
        self.terminate_grace = terminate_grace
        self.measure_interval = measure_interval

    @staticmethod
    def supports_limits() -> bool:
        """Whether this host can enforce an aggregate limit on a process tree.

        Per-process rlimits would be available almost anywhere, but they bound a
        different thing than the caller declared -- per-process rather than
        per-tree CPU, virtual rather than resident memory -- so they are not
        used here, and the honest answer for a host without ``/proc`` is that
        the limit cannot be kept.
        """
        return sys.platform.startswith("linux") and Path("/proc").is_dir()

    def run(
        self,
        command: Sequence[str],
        *,
        log_path: str | Path,
        timeout_seconds: float,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        on_line: Callable[[str], None] | None = None,
        limits: ResourceRequest | None = None,
        on_started: Callable[[int, int, int | None], None] | None = None,
    ) -> ProcessOutcome:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if limits is not None and limits.declared and not self.supports_limits():
            raise ValueError(
                "this host cannot measure a process tree, so the requested "
                f"limits ({limits.describe()}) cannot be enforced"
            )

        normalized = tuple(str(item) for item in command)
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        timed_out = False
        cancelled = False
        exceeded: str | None = None
        next_measurement = started
        peak_cpu = 0.0
        peak_memory = 0
        peak_processes = 0

        with path.open("a", encoding="utf-8") as log:
            process = subprocess.Popen(
                normalized,
                cwd=str(cwd) if cwd else None,
                env=dict(env) if env is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                # Its own session, so signalling the group reaches every
                # descendant rather than just the direct child.
                start_new_session=(os.name == "posix"),
            )
            if on_started is not None:
                try:
                    try:
                        process_group_id = os.getpgid(process.pid) if os.name == "posix" else process.pid
                    except OSError:
                        # A very short-lived adapter may have exited between
                        # Popen and this callback; its pid is still a safe
                        # identity to record, and the group is already gone.
                        process_group_id = process.pid
                    on_started(
                        process.pid,
                        process_group_id,
                        self._start_ticks(process.pid),
                    )
                except BaseException:
                    self._terminate_tree(process)
                    process.wait()
                    raise
            lines: queue.Queue[str] = queue.Queue(maxsize=TELEMETRY_QUEUE_SIZE)
            dropped_telemetry = [0]
            observer_errors: list[str] = []
            reader = threading.Thread(
                target=self._read_output,
                args=(process.stdout, log, lines, dropped_telemetry),
                daemon=True, name=f"output-{process.pid}",
            )
            reader.start()

            try:
                while process.poll() is None:
                    self._drain(lines, on_line, DRAIN_BATCH, observer_errors)
                    if cancel_requested is not None and cancel_requested():
                        cancelled = True
                        self._terminate_tree(process)
                        break
                    if time.monotonic() - started >= timeout_seconds:
                        timed_out = True
                        self._terminate_tree(process)
                        break
                    now = time.monotonic()
                    if now >= next_measurement:
                        next_measurement = now + self.measure_interval
                        measured = self.measure(process.pid)
                        peak_processes = max(peak_processes, measured[0])
                        peak_cpu = max(peak_cpu, measured[1])
                        peak_memory = max(peak_memory, measured[2])
                        if limits is not None and limits.declared:
                            exceeded = self._breach_values(measured, limits)
                        if exceeded is not None:
                            self._terminate_tree(process)
                            break
                    time.sleep(self.poll_interval)
            except BaseException:
                # An interrupted controller must not orphan a many-core job.
                self._terminate_tree(process)
                raise

            try:
                process.wait(timeout=self.terminate_grace)
            except subprocess.TimeoutExpired:
                self._kill_tree(process)
                process.wait()

            reader.join(timeout=1.0)
            if self._drain(lines, on_line, FINAL_DRAIN_BATCH, observer_errors) == FINAL_DRAIN_BATCH:
                log.write("\n[guardian] telemetry truncated after termination\n")
            if dropped_telemetry[0]:
                log.write("\n[guardian] telemetry dropped "
                          f"{dropped_telemetry[0]} lines; raw output is complete\n")
            log.write("".join(observer_errors))
            if timed_out:
                log.write(f"\n[guardian] wall-clock timeout after {timeout_seconds:.3f}s\n")
            if cancelled:
                log.write("\n[guardian] cancellation requested\n")
            if exceeded is not None:
                log.write(f"\n[guardian] resource limit exceeded: {exceeded}\n")
            log.flush()

        return ProcessOutcome(
            command=normalized,
            returncode=process.returncode,
            seconds=time.monotonic() - started,
            timed_out=timed_out,
            cancelled=cancelled,
            exceeded=exceeded,
            cpu_seconds=peak_cpu,
            peak_memory_bytes=peak_memory,
            peak_processes=peak_processes,
        )

    @staticmethod
    def _start_ticks(pid: int) -> int | None:
        try:
            raw = Path(f"/proc/{pid}/stat").read_text()
            fields = raw[raw.rfind(")") + 2:].split()
            return int(fields[19])
        except (OSError, ValueError, IndexError):
            return None

    @staticmethod
    def _read_output(
        stream: TextIO | None,
        log: TextIO,
        lines: queue.Queue[str],
        dropped_telemetry: list[int],
    ) -> None:
        try:
            if stream is not None:
                for line in iter(stream.readline, ""):
                    log.write(line)
                    try:
                        lines.put_nowait(line)
                    except queue.Full:
                        dropped_telemetry[0] += 1
        finally:
            if stream is not None:
                stream.close()

    @staticmethod
    def _drain(
        lines: queue.Queue[str], on_line: Callable[[str], None] | None,
        max_lines: int, observer_errors: list[str],
    ) -> int:
        count = 0
        batch: list[str] = []
        while count < max_lines:
            try:
                line = lines.get_nowait()
            except queue.Empty:
                break
            batch.append(line)
            count += 1
        if batch and on_line is not None:
            for line in batch:
                try:
                    on_line(line)
                except Exception as exc:  # noqa: BLE001
                    observer_errors.append(
                        "[guardian] observer failed: "
                        f"{type(exc).__name__}: {exc}\n"
                    )
        return count
    def _breach(self, root_pid: int, limits: ResourceRequest) -> str | None:
        """Which declared limit the tree is past, or ``None``.

        Process count is checked first because it is the cheapest to see and the
        one a runaway driver breaks first; the message names both the
        measurement and the request, because "limit exceeded" without the two
        numbers is not something an operator can act on.
        """
        return self._breach_values(self.measure(root_pid), limits)

    @staticmethod
    def _breach_values(measured: tuple[int, float, int], limits: ResourceRequest) -> str | None:
        processes, cpu_seconds, memory_bytes = measured
        if limits.processes is not None and processes > limits.processes:
            return (
                f"processes: the tree held {processes}, above the requested "
                f"{limits.processes}"
            )
        if limits.cpu_seconds is not None and cpu_seconds > limits.cpu_seconds:
            return (
                f"cpu_seconds: the tree used {cpu_seconds:.2f}s, above the "
                f"requested {limits.cpu_seconds:g}s"
            )
        if limits.memory_bytes is not None and memory_bytes > limits.memory_bytes:
            return (
                f"memory_bytes: the tree held {memory_bytes} bytes resident, "
                f"above the requested {limits.memory_bytes}"
            )
        return None

    def measure(self, root_pid: int) -> tuple[int, float, int]:
        """The tree's process count, CPU seconds and resident bytes.

        Aggregate, not per-process, because that is what the caller declared:
        an EDA flow is usually one process that grows, or a driver that spawns
        hundreds, and a per-process reading misses the second case entirely.

        ``cutime`` and ``cstime`` are added to every live process's own CPU
        because a reaped child's time moves into its parent and would otherwise
        vanish from the total the moment the child exits -- which is exactly
        when a flow that forks per stage spends most of its CPU.
        """
        ticks = _sysconf("SC_CLK_TCK", 100)
        page = _sysconf("SC_PAGE_SIZE", 4096)
        processes = 0
        cpu_ticks = 0
        resident_pages = 0
        for pid in self._process_tree(root_pid):
            try:
                stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
                tail = stat[stat.rfind(")") + 2:].split()
                if not tail or tail[0] == "Z":
                    # A zombie holds no memory and no future CPU.
                    continue
                cpu_ticks += sum(int(tail[index]) for index in (11, 12, 13, 14))
                statm = Path(f"/proc/{pid}/statm").read_text(
                    encoding="utf-8"
                ).split()
                resident_pages += int(statm[1])
                processes += 1
            except (FileNotFoundError, PermissionError, ProcessLookupError,
                    ValueError, IndexError, OSError):
                # A process that exits mid-walk is not an error; the next
                # measurement will not see it, and this one is still a truthful
                # lower bound.
                continue
        return processes, cpu_ticks / ticks, resident_pages * page

    # -- process tree ------------------------------------------------------

    def _terminate_tree(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if os.name == "posix":
            targets = self._process_tree(process.pid)
            self._signal_processes(targets, signal.SIGTERM)
        else:  # pragma: no cover - the platform is Linux
            process.terminate()
            targets = {process.pid}

        deadline = time.monotonic() + self.terminate_grace
        while time.monotonic() < deadline:
            root_done = process.poll() is not None
            descendants_done = os.name != "posix" or not any(
                self._process_is_running(pid) for pid in targets if pid != process.pid
            )
            if root_done and descendants_done:
                return
            time.sleep(min(self.poll_interval, 0.05))
        self._kill_tree(process, targets)

    @classmethod
    def _kill_tree(
        cls, process: subprocess.Popen[str], targets: set[int] | None = None
    ) -> None:
        if os.name == "posix":
            cls._signal_processes(
                targets if targets is not None else cls._process_tree(process.pid),
                signal.SIGKILL,
            )
        elif process.poll() is None:  # pragma: no cover
            process.kill()

    @staticmethod
    def _process_is_running(pid: int) -> bool:
        """True unless the pid is gone or a zombie."""
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            return False
        tail = stat[stat.rfind(")") + 2:].split()
        return bool(tail) and tail[0] != "Z"

    @staticmethod
    def _process_tree(root_pid: int) -> set[int]:
        """Every descendant of root_pid, including ones that called setsid().

        Linux exposes a per-task children index; following it is proportional
        to the size of this tree.  Scanning all of /proc made a 250 ms
        cancellation take seconds on a busy EDA host, which is a real
        regression for the user waiting on Ctrl-C.
        """
        if not Path("/proc").is_dir():  # pragma: no cover - non-Linux
            return {root_pid}
        result = {root_pid}
        pending = [root_pid]
        indexed = True
        while pending:
            parent = pending.pop()
            try:
                child_ids = Path(
                    f"/proc/{parent}/task/{parent}/children"
                ).read_text(encoding="utf-8").split()
            except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
                if parent == root_pid:
                    indexed = False
                continue
            for value in child_ids:
                try:
                    child = int(value)
                except ValueError:
                    continue
                if child not in result:
                    result.add(child)
                    pending.append(child)
        if indexed:
            return result

        # Kernels without the children index: build the map from /proc/*/stat.
        children: dict[int, list[int]] = {}
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / "stat").read_text(encoding="utf-8")
                tail = stat[stat.rfind(")") + 2:].split()
                children.setdefault(int(tail[1]), []).append(int(entry.name))
            except (FileNotFoundError, PermissionError, ProcessLookupError,
                    ValueError, IndexError, OSError):
                continue
        result = {root_pid}
        pending = [root_pid]
        while pending:
            parent = pending.pop()
            for child in children.get(parent, ()):
                if child not in result:
                    result.add(child)
                    pending.append(child)
        return result

    @staticmethod
    def _signal_processes(pids: set[int], signum: int) -> None:
        """Signal each descendant's process group, never our own."""
        own_group = os.getpgrp()
        groups = set()
        for pid in pids:
            try:
                group = os.getpgid(pid)
            except (ProcessLookupError, PermissionError, OSError):
                continue
            if group != own_group:
                groups.add(group)
        for group in groups:
            try:
                os.killpg(group, signum)
            except (ProcessLookupError, PermissionError, OSError):
                pass


def _sysconf(name: str, fallback: int) -> int:
    """A sysconf value, or a documented fallback.

    Only the fallback is documented here because the values are only used to
    turn counts into seconds and bytes for a limit comparison; a host that
    cannot report them is a host where ``supports_limits`` is already false.
    """
    try:
        return int(os.sysconf(name))
    except (ValueError, OSError, AttributeError):  # pragma: no cover
        return fallback
