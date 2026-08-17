#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export learning observations (live + historical) to JSON/CSV for the teacher report."""
import csv
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for source in (ROOT / "packages/contracts/src", ROOT / "packages/analysis/src"):
    sys.path.insert(0, str(source))

OUT = Path(os.environ.get("EXPORT_OUT", ROOT / "var" / "export"))

LIVE_DB = ROOT / "var" / "public" / "tenant-learning.db"
P14_DB = ROOT / "artifacts" / "p14-real-20260806" / "learning_observations.db"
E2E_DBS = sorted((ROOT / "var" / "public").glob("tenant-learning*.db"))

from openroad_platform_contracts import LearningObservation  # noqa: E402


def rows_from(db: Path, table: str) -> list[dict]:
    if not db.exists():
        return []
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(f"SELECT * FROM {table}").fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def flatten(row: dict) -> dict:
    """Merge payload_json into top-level fields for readable export."""
    out = dict(row)
    payload = row.get("payload_json")
    if payload:
        try:
            data = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            data = {}
        for key, value in data.items():
            out.setdefault(key, value)
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    records = []

    # live tenant store
    for db in [LIVE_DB]:
        for row in rows_from(db, "tenant_observations_v1"):
            flat = flatten(row)
            records.append({"source": f"live:{db.name}", **flat})
    # p14 historical
    for row in rows_from(P14_DB, "learning_observations_v1"):
        flat = flatten(row)
        records.append({"source": "p14-historical", **flat})

    # dedupe by observation_id
    seen = set()
    unique = []
    for rec in records:
        oid = rec.get("observation_id") or rec.get("observationId") or ""
        if oid and oid in seen:
            continue
        if oid:
            seen.add(oid)
        unique.append(rec)

    manifest = {
        "exported_at": __import__("datetime").datetime.now(timezone := __import__("datetime").timezone.utc).isoformat(),
        "record_count": len(unique),
        "sources": ["var/public/tenant-learning.db (live)",
                    "artifacts/p14-real-20260806/learning_observations.db (historical)"],
        "schema_note": ("Observed-only learning observations; fields follow the "
                        "LearningObservation contract (design/PDK/toolchain/metrics/fingerprint)."),
    }

    with open(OUT / "learning_observations.json", "w", encoding="utf-8") as f:
        json.dump({"manifest": manifest, "observations": unique}, f,
                  ensure_ascii=False, indent=2, default=str)
    print(f"JSON: {OUT / 'learning_observations.json'} ({len(unique)} records)")

    if unique:
        keys = sorted({k for r in unique for k in r.keys()})
        with open(OUT / "learning_observations.csv", "w", encoding="utf-8-sig",
                  newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            for rec in unique:
                writer.writerow({k: (json.dumps(v, ensure_ascii=False, default=str)
                                     if isinstance(v, (dict, list)) else v)
                                 for k, v in rec.items()})
        print(f"CSV: {OUT / 'learning_observations.csv'}")

    # summary stats for the report
    designs = {}
    for rec in unique:
        ctx = rec.get("context") or {}
        design = ctx.get("design_id") or rec.get("design_id") or "?"
        designs.setdefault(design, 0)
        designs[design] += 1
    print("designs:", json.dumps(designs, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
