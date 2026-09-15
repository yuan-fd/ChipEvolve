"""Bounded read-only log query for running or completed attempts."""
from __future__ import annotations

from pathlib import Path

from .adapter import LOG_FILENAME
from .store import RuntimeStore, RuntimeStoreError


class LogQuery:
    def __init__(self, store: RuntimeStore, *, max_bytes: int = 1 << 20):
        self.store = store
        self.max_bytes = max_bytes

    def run(self, run_id: str, *, offset: int = 0, max_bytes: int | None = None) -> dict[str, object]:
        if offset < 0:
            raise ValueError("offset must be non-negative")
        limit = self.max_bytes if max_bytes is None else max_bytes
        if not 0 < limit <= self.max_bytes:
            raise ValueError(f"max_bytes must be between 1 and {self.max_bytes}")
        detail = self.store.describe_run(run_id)
        attempts = [a for stage in detail["stages"] for a in stage["attempts"]]
        chunks = []
        for attempt in attempts:
            path = Path(attempt["workspace"]) / LOG_FILENAME
            if path.is_file():
                with path.open("rb") as stream:
                    stream.seek(offset)
                    chunks.append(stream.read(limit))
        return {"run": run_id, "offset": offset, "max_bytes": limit,
                "content": b"".join(chunks).decode("utf-8", errors="replace")}
