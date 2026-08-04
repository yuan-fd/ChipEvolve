#!/usr/bin/env python3
"""Adapter fixture that deliberately reports an artifact outside its workspace."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    timestamp = datetime.now(timezone.utc).isoformat()
    args.result.write_text(json.dumps({
        "schema_version": 1,
        "status": "succeeded",
        "exit_code": 0,
        "started_at": timestamp,
        "ended_at": timestamp,
        "metrics": [],
        "artifacts": [{"kind": "report", "path": "../escape.json"}],
        "failure": None,
        "provenance": {},
    }), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
