#!/usr/bin/env python3
"""Integrity-checked SQLite backup and non-destructive restore for platform state."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def integrity(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])


def backup(databases: list[Path], output: Path) -> Path:
    if not databases:
        raise ValueError("At least one database is required")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError(f"Backup output must be an empty directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    target_root = output / "databases"
    target_root.mkdir()
    records = []
    seen = set()
    for source in databases:
        source = source.expanduser().resolve()
        if not source.is_file() or source.suffix != ".db":
            raise ValueError(f"Not a SQLite database file: {source}")
        name = source.name
        if name in seen:
            raise ValueError(f"Duplicate database filename: {name}")
        seen.add(name)
        target = target_root / name
        with sqlite3.connect(source) as source_db, sqlite3.connect(target) as target_db:
            source_db.backup(target_db)
        check = integrity(target)
        if check != "ok":
            raise RuntimeError(f"Backup integrity check failed for {name}: {check}")
        records.append({"name": name, "source": str(source),
                        "path": f"databases/{name}", "size_bytes": target.stat().st_size,
                        "sha256": sha256(target), "integrity_check": check})
    manifest = output / "backup.manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "databases": records, "restore_policy": "new-empty-directory-only",
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def restore(manifest: Path, target_root: Path) -> list[Path]:
    manifest = manifest.expanduser().resolve()
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported backup manifest")
    if target_root.exists() and (not target_root.is_dir() or any(target_root.iterdir())):
        raise FileExistsError(f"Restore target must be an empty directory: {target_root}")
    target_root.mkdir(parents=True, exist_ok=True)
    restored = []
    for record in payload.get("databases", []):
        name = str(record.get("name") or "")
        if not name or Path(name).name != name:
            raise ValueError("Backup manifest contains an unsafe database name")
        source = (manifest.parent / record["path"]).resolve()
        if source.parent != (manifest.parent / "databases").resolve():
            raise ValueError("Backup manifest path escaped its database directory")
        if sha256(source) != record["sha256"] or source.stat().st_size != record["size_bytes"]:
            raise ValueError(f"Backup hash/size mismatch: {record['name']}")
        target = target_root / name
        shutil.copy2(source, target)
        if integrity(target) != "ok":
            raise RuntimeError(f"Restored database is corrupt: {target}")
        restored.append(target)
    return restored


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("backup")
    create.add_argument("--database", type=Path, action="append", required=True)
    create.add_argument("--output", type=Path, required=True)
    recover = subparsers.add_parser("restore")
    recover.add_argument("--manifest", type=Path, required=True)
    recover.add_argument("--target-root", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "backup":
        result = backup(args.database, args.output)
        print(result)
    else:
        for result in restore(args.manifest, args.target_root):
            print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
