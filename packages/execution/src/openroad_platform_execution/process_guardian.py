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
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
        else:
            proc.terminate()

        try:
            proc.wait(timeout=self.terminate_grace)
        except subprocess.TimeoutExpired:
            self._kill_tree(proc)

    @staticmethod
    def _kill_tree(proc: subprocess.Popen[str]) -> None:
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif proc.poll() is None:
            proc.kill()

