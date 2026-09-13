"""Run one command under a wall-clock deadline and clean up its whole tree.

This is the platform's safety net.  An EDA job can spawn hundreds of processes;
if a timeout leaves them running, the next experiment competes with a ghost for
the same cores and its measurements are quietly wrong.

Two properties are non-negotiable and each has a test:

1. **A deadline is enforced even if the child never writes a byte.** A silent or
   enormously noisy process must not be able to outlive its budget.
2. **The whole descendant tree dies, including children that called setsid().**
   A process that deliberately detaches is still ours to clean up.

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
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence, TextIO

#: How long a child gets to exit after SIGTERM before SIGKILL.
DEFAULT_TERMINATE_GRACE = 5.0

#: How often the supervisor wakes to check the deadline and cancellation.
DEFAULT_POLL_INTERVAL = 0.1

#: Lines drained per wake-up.  Draining without a bound lets a permanently
#: non-empty queue starve deadline enforcement; a permanently noisy process
#: would then never time out.
DRAIN_BATCH = 32

#: Lines preserved from the tail once the process is gone.
FINAL_DRAIN_BATCH = 128


@dataclass(frozen=True)
class ProcessOutcome:
    command: tuple[str, ...]
    returncode: int
    seconds: float
    timed_out: bool = False
    cancelled: bool = False


class ProcessGuardian:
    def __init__(
        self, *,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        terminate_grace: float = DEFAULT_TERMINATE_GRACE,
    ):
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        if terminate_grace <= 0:
            raise ValueError("terminate_grace must be positive")
        self.poll_interval = poll_interval
        self.terminate_grace = terminate_grace

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
    ) -> ProcessOutcome:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        normalized = tuple(str(item) for item in command)
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        timed_out = False
        cancelled = False

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
            lines: queue.Queue[str | None] = queue.Queue()
            reader = threading.Thread(
                target=self._read_output, args=(process.stdout, lines),
                daemon=True, name=f"output-{process.pid}",
            )
            reader.start()

            try:
                while process.poll() is None:
                    self._drain(lines, log, on_line, DRAIN_BATCH)
                    if cancel_requested is not None and cancel_requested():
                        cancelled = True
                        self._terminate_tree(process)
                        break
                    if time.monotonic() - started >= timeout_seconds:
                        timed_out = True
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
            if self._drain(lines, log, on_line, FINAL_DRAIN_BATCH) == FINAL_DRAIN_BATCH:
                log.write("\n[guardian] output truncated after termination\n")
            if timed_out:
                log.write(
                    f"\n[guardian] wall-clock timeout after {timeout_seconds:.3f}s\n"
                )
            if cancelled:
                log.write("\n[guardian] cancellation requested\n")
            log.flush()

        return ProcessOutcome(
            command=normalized,
            returncode=process.returncode,
            seconds=time.monotonic() - started,
            timed_out=timed_out,
            cancelled=cancelled,
        )

    # -- output ------------------------------------------------------------

    @staticmethod
    def _read_output(stream: TextIO | None, lines: "queue.Queue[str | None]") -> None:
        try:
            if stream is not None:
                for line in iter(stream.readline, ""):
                    lines.put(line)
        finally:
            if stream is not None:
                stream.close()
            lines.put(None)

    @staticmethod
    def _drain(
        lines: "queue.Queue[str | None]", log: TextIO,
        on_line: Callable[[str], None] | None, max_lines: int,
    ) -> int:
        count = 0
        batch: list[str] = []
        while count < max_lines:
            try:
                line = lines.get_nowait()
            except queue.Empty:
                break
            if line is None:
                continue
            batch.append(line)
            count += 1
        if batch:
            # One write: per-line writes on a network filesystem can turn a
            # chatty tool into a way to defeat its own deadline.
            log.write("".join(batch))
            if on_line is not None:
                for line in batch:
                    try:
                        on_line(line)
                    except Exception as exc:  # noqa: BLE001
                        # Telemetry is not authority.  A failing observer must
                        # never be able to destroy a protected experiment, so
                        # the failure is recorded next to the raw log and the
                        # tool result stands on its own.
                        log.write(
                            "[guardian] observer failed: "
                            f"{type(exc).__name__}: {exc}\n"
                        )
        return count

    # -- process tree ------------------------------------------------------

    def _terminate_tree(self, process: "subprocess.Popen[str]") -> None:
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
        cls, process: "subprocess.Popen[str]", targets: set[int] | None = None
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
