"""Generic artifact inventory view; values are opaque plugin metadata."""
from __future__ import annotations

from typing import Any

from .store import RuntimeStore


class ArtifactInventory:
    def __init__(self, store: RuntimeStore):
        self.store = store

    def for_run(self, run_id: str, *, category: str | None = None,
                format: str | None = None, stage: str | None = None
                ) -> list[dict[str, Any]]:
        view = self.store.describe_run(run_id)
        result: list[dict[str, Any]] = []
        for stage_view in view["stages"]:
            for attempt in stage_view["attempts"]:
                for artifact in attempt["artifacts"]:
                    metadata = artifact.get("metadata") or {}
                    if category is not None and metadata.get("category") != category:
                        continue
                    if format is not None and metadata.get("format") != format:
                        continue
                    if stage is not None and metadata.get("stage") != stage:
                        continue
                    result.append({**artifact, "run_id": run_id,
                                   "stage_key": stage_view["stage_key"],
                                   "attempt_id": attempt["attempt_id"]})
        return result
