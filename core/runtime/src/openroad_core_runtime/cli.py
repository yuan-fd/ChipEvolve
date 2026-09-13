"""Minimal operator entry point for the runtime kernel.

Deliberately small: this exists so the kernel is runnable and observable on its
own, not so it becomes a second application.  Anything richer belongs in an app.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .store import RuntimeStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openroad-runtime")
    parser.add_argument("--db", required=True, help="runtime database path")
    sub = parser.add_subparsers(dest="command", required=True)

    show = sub.add_parser("show", help="print the stored view of one run")
    show.add_argument("run_id")

    sub.add_parser("runs", help="list run ids and statuses")

    sub.add_parser("reclaim", help="mark attempts with an expired lease as lost")

    args = parser.parse_args(argv)
    store = RuntimeStore(Path(args.db))
    try:
        if args.command == "show":
            print(json.dumps(store.describe_run(args.run_id), indent=2))
        elif args.command == "runs":
            with store._lock:  # noqa: SLF001 - an operator view of the same file
                rows = store._connection.execute(  # noqa: SLF001
                    "SELECT run_id, status, task_id FROM runtime_runs "
                    "ORDER BY created_at"
                ).fetchall()
            for row in rows:
                print(f"{row['run_id']}  {row['status']:<16} {row['task_id']}")
        elif args.command == "reclaim":
            reclaimed = store.reclaim_expired_attempts()
            print(json.dumps(reclaimed))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
