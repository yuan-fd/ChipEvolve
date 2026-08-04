from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from openroad_platform_contracts import RunRequest, RunStage

from .store import JobStore
from .worker import Worker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenROAD Platform durable job queue")
    parser.add_argument("--db", type=Path, default=Path("var/platform.db"))
    commands = parser.add_subparsers(dest="command", required=True)

    submit = commands.add_parser("submit")
    submit.add_argument("rtl", type=Path)
    submit.add_argument("--top")
    submit.add_argument("--clock")
    submit.add_argument("--period", type=float, default=10.0)
    submit.add_argument("--platform", default="nangate45")
    submit.add_argument("--stage", choices=[item.value for item in RunStage], default="finish")
    submit.add_argument("--core-utilization", type=float, default=10.0)
    submit.add_argument("--place-density", type=float, default=0.45)
    submit.add_argument("--timeout", type=int, default=3600)

    worker = commands.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--poll-seconds", type=float, default=1.0)
    worker.add_argument("--work-root", type=Path, default=Path("var/runs"))
    worker.add_argument("--orfs-root", type=Path,
                        default=Path(os.environ.get("ORFS_ROOT", Path.home() / "OpenROAD-flow-scripts")))
    worker.add_argument("--openroad-bin", type=Path, default=None)
    worker.add_argument("--yosys-bin", type=Path, default=None)

    commands.add_parser("list").add_argument("--limit", type=int, default=50)
    status = commands.add_parser("status")
    status.add_argument("job_id")
    cancel = commands.add_parser("cancel")
    cancel.add_argument("job_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = JobStore(args.db)
    if args.command == "submit":
        job = store.submit(RunRequest(
            rtl_path=str(args.rtl.expanduser().resolve()),
            top=args.top,
            clock=args.clock,
            clock_period_ns=args.period,
            platform=args.platform,
            target_stage=RunStage(args.stage),
            core_utilization_pct=args.core_utilization,
            place_density=args.place_density,
            stage_timeout_seconds=args.timeout,
        ))
        print(job.id)
        return 0
    if args.command == "worker":
        worker = Worker(
            store,
            orfs_root=args.orfs_root,
            work_root=args.work_root,
            openroad_bin=args.openroad_bin,
            yosys_bin=args.yosys_bin,
        )
        if args.once:
            return 0 if worker.run_once() else 3
        worker.serve(poll_seconds=args.poll_seconds)
        return 0
    if args.command == "list":
        for job in store.list(limit=args.limit):
            print(f"{job.id}\t{job.status.value}\t{job.request.top or '-'}\t{job.updated_at}")
        return 0
    if args.command == "status":
        job = store.get(args.job_id)
        print(json.dumps({
            "id": job.id,
            "status": job.status.value,
            "request": job.request.to_dict(),
            "result": job.result,
            "error": job.error,
            "events": store.events(job.id),
        }, indent=2, ensure_ascii=False))
        return 0
    if args.command == "cancel":
        print(store.request_cancel(args.job_id).status.value)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
