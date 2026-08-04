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


@dataclass(frozen=True)
class ProcessOutcome:
    command: tuple[str, ...]
    returncode: int
    seconds: float
    timed_out: bool = False
    cancelled: bool = False


class ProcessGuardian:
    """Run one command with wall-clock enforcement and process-tree cleanup."""

    def __init__(self, *, poll_interval: float = 0.1, terminate_grace: float = 5.0):
        self.poll_interval = poll_interval
        self.terminate_grace = terminate_grace

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        log_path: str | Path,
        timeout_seconds: float,
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
            proc = subprocess.Popen(
                normalized,
                cwd=str(cwd) if cwd else None,
                env=dict(env) if env is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=(os.name == "posix"),
            )
            lines: queue.Queue[str | None] = queue.Queue()
            reader = threading.Thread(
                target=self._read_output,
                args=(proc.stdout, lines),
                daemon=True,
                name=f"process-output-{proc.pid}",
            )
            reader.start()

            while proc.poll() is None:
                self._drain(lines, log, on_line)
                elapsed = time.monotonic() - started
                if cancel_requested is not None and cancel_requested():
                    cancelled = True
                    self._terminate_tree(proc)
                    break
                if elapsed >= timeout_seconds:
                    timed_out = True
                    self._terminate_tree(proc)
                    break
                time.sleep(self.poll_interval)

            try:
                proc.wait(timeout=self.terminate_grace)
            except subprocess.TimeoutExpired:
                self._kill_tree(proc)
                proc.wait()

            reader.join(timeout=1.0)
            self._drain(lines, log, on_line)
            if timed_out:
                log.write(f"\n[guardian] wall-clock timeout after {timeout_seconds:.3f}s\n")
            if cancelled:
                log.write("\n[guardian] cancellation requested\n")
            log.flush()

        return ProcessOutcome(
            command=normalized,
            returncode=proc.returncode,
            seconds=time.monotonic() - started,
            timed_out=timed_out,
            cancelled=cancelled,
        )

    @staticmethod
    def _read_output(stream: TextIO | None, lines: queue.Queue[str | None]) -> None:
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
        lines: queue.Queue[str | None],
        log: TextIO,
        on_line: Callable[[str], None] | None,
    ) -> None:
        while True:
            try:
                line = lines.get_nowait()
            except queue.Empty:
                return
            if line is None:
                continue
            log.write(line)
            if on_line is not None:
                on_line(line)

    def _terminate_tree(self, proc: subprocess.Popen[str]) -> None:
        if proc.poll() is not None:
            return
        if os.name == "posix":
            targets = self._process_tree(proc.pid)
            self._signal_processes(targets, signal.SIGTERM)
        else:
            proc.terminate()
            targets = {proc.pid}

        deadline = time.monotonic() + self.terminate_grace
        while time.monotonic() < deadline:
            root_done = proc.poll() is not None
            descendants_done = os.name != "posix" or not any(
                self._process_is_running(pid) for pid in targets if pid != proc.pid
            )
            if root_done and descendants_done:
                return
            time.sleep(min(self.poll_interval, 0.05))
        self._kill_tree(proc, targets)

    @classmethod
    def _kill_tree(cls, proc: subprocess.Popen[str], targets: set[int] | None = None) -> None:
        if os.name == "posix":
            cls._signal_processes(targets or cls._process_tree(proc.pid), signal.SIGKILL)
        elif proc.poll() is None:
            proc.kill()

    @staticmethod
    def _process_is_running(pid: int) -> bool:
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            return False
        tail = stat[stat.rfind(")") + 2:].split()
        return bool(tail) and tail[0] != "Z"

    @staticmethod
    def _process_tree(root_pid: int) -> set[int]:
        """Snapshot Linux descendants, including children that called setsid()."""
        if not Path("/proc").is_dir():
            return {root_pid}
        children: dict[int, list[int]] = {}
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / "stat").read_text(encoding="utf-8")
                tail = stat[stat.rfind(")") + 2:].split()
                ppid = int(tail[1])
                pid = int(entry.name)
            except (FileNotFoundError, PermissionError, ProcessLookupError,
                    ValueError, IndexError):
                continue
            children.setdefault(ppid, []).append(pid)
        result = {root_pid}
        pending = [root_pid]
        while pending:
            parent = pending.pop()
            for child in children.get(parent, []):
                if child not in result:
                    result.add(child)
                    pending.append(child)
        return result

    @staticmethod
    def _signal_processes(pids: set[int], signum: signal.Signals) -> None:
        """Signal every descendant session/process group."""
        own_group = os.getpgrp()
        groups = set()
        for pid in pids:
            try:
                group = os.getpgid(pid)
            except ProcessLookupError:
                continue
            if group != own_group:
                groups.add(group)
        for group in groups:
            try:
                os.killpg(group, signum)
            except ProcessLookupError:
                pass
