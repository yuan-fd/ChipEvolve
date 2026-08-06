from __future__ import annotations

import json
import sqlite3

import pytest

from scripts.platform_state import backup, restore


def test_database_backup_restore_is_hash_checked_and_non_destructive(tmp_path):
    source = tmp_path / "runtime.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE runs(id TEXT PRIMARY KEY, status TEXT)")
        connection.execute("INSERT INTO runs VALUES ('run-1', 'succeeded')")
    manifest = backup([source], tmp_path / "backup")
    restored = restore(manifest, tmp_path / "restored")
    with sqlite3.connect(restored[0]) as connection:
        assert connection.execute("SELECT status FROM runs WHERE id='run-1'").fetchone()[0] == "succeeded"
    with pytest.raises(FileExistsError):
        restore(manifest, tmp_path / "restored")


def test_restore_rejects_tampered_database(tmp_path):
    source = tmp_path / "campaign.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE campaigns(id TEXT)")
    manifest = backup([source], tmp_path / "backup")
    payload = json.loads(manifest.read_text())
    backed_up = manifest.parent / payload["databases"][0]["path"]
    backed_up.write_bytes(backed_up.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="hash/size"):
        restore(manifest, tmp_path / "restore-tampered")
