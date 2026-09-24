"""Process identity checks used before reclaiming a stale attempt."""

from __future__ import annotations

import os
import signal
import sqlite3
import time
from pathlib import Path


def fence_expired_process(row: sqlite3.Row) -> bool:
    """Stop a recorded adapter group, returning true only when it is gone."""
    if not _alive(row):
        return True
    try:
        os.killpg(int(row["process_group_id"]), signal.SIGTERM)
    except OSError:
        return not _alive(row)
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        if not _alive(row):
            return True
        time.sleep(0.02)
    try:
        os.killpg(int(row["process_group_id"]), signal.SIGKILL)
    except OSError:
        pass
    return not _alive(row)


def _alive(row: sqlite3.Row) -> bool:
    process_id = row["process_id"]
    process_group_id = row["process_group_id"]
    if process_id is None and process_group_id is None:
        return False
    if process_id is not None and row["process_start_ticks"] is not None:
        try:
            raw = Path(f"/proc/{int(process_id)}/stat").read_text()
            fields = raw[raw.rfind(")") + 2:].split()
            if fields[0] == "Z":
                return False
            if int(fields[19]) != int(row["process_start_ticks"]):
                return False
        except (OSError, ValueError, IndexError):
            return False
    try:
        os.killpg(int(process_group_id), 0)
    except (OSError, ValueError):
        return False
    return True
