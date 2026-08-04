#!/usr/bin/env python3
"""Restore a Runtime SQLite snapshot and verify run/artifact readability."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "packages/contracts/src", ROOT / "packages/execution/src",
               ROOT / "packages/scheduler/src"):
    sys.path.insert(0, str(source))

from openroad_platform_scheduler import RuntimeStore  # noqa: E402


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    snapshot = args.snapshot.expanduser().resolve()
    restore_root = Path("/tmp") / f"openroad-platform-runtime-restore-{uuid.uuid4().hex}"
    restore_root.mkdir(parents=True)
    restored = restore_root / "runtime.db"
    with sqlite3.connect(snapshot) as source, sqlite3.connect(restored) as destination:
        source.backup(destination)
    with sqlite3.connect(restored) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    store = RuntimeStore(restored)
    view = store.describe_run(args.run_id)
    artifacts = []
    for stage in view["stages"]:
        for attempt in stage["attempts"]:
            workspace = Path(attempt["workspace"])
            for item in attempt["artifacts"]:
                path = (workspace / item["store_key"]).resolve()
                verified = path.is_file() and _sha(path) == item["sha256"]
                artifacts.append({"artifact_id": item["artifact_id"],
                                  "store_key": item["store_key"], "verified": verified})
    accepted = (integrity == "ok" and not foreign_keys
                and view["run"]["status"] == "succeeded"
                and artifacts and all(item["verified"] for item in artifacts))
    if not accepted:
        raise RuntimeError("Restored Runtime snapshot failed validation")
    payload = {
        "schema_version": 1, "phase": "P8-Real-backup-restore", "accepted": True,
        "source_snapshot": str(snapshot), "source_sha256": _sha(snapshot),
        "restored_db": str(restored), "restored_under_tmp": True,
        "integrity_check": integrity, "foreign_key_violations": len(foreign_keys),
        "run_id": args.run_id, "run_status": view["run"]["status"],
        "artifact_count": len(artifacts), "all_artifacts_verified": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
