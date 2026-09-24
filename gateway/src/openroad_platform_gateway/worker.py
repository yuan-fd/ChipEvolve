"""Run a worker against a state root.

    python3 -m openroad_platform_gateway.worker --state-root ... --once

The worker is a process, not a package: it needs a store, a registry, an
evaluator and a runtime, and assembling those is the composition root's job.  It
used to live in ``openroad_platform_runtime`` with its own copy of that
assembly, which made the runtime package depend on the registry and the
evaluator -- and the evaluator depends on the runtime, so the copy was hidden
behind an import inside a function, where nothing would notice it.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import sys
import threading

from openroad_platform_runtime import RuntimeWorker

from .bootstrap import KernelPaths, build_kernel_parts

LOGGER = logging.getLogger("openroad_platform_gateway.worker")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openroad-platform-worker")
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--plugins-root", default="plugins")
    parser.add_argument(
        "--admissions-root",
        default="admissions",
        help="the platform's own plugin trust records",
    )
    parser.add_argument("--idle-seconds", type=float, default=0.5)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument(
        "--worker-id",
        default=None,
        help="stable worker identity; defaults to host and pid",
    )
    parser.add_argument("--capacity-cpu-cores", type=int)
    parser.add_argument("--capacity-memory-bytes", type=int)
    parser.add_argument("--platform-fraction", type=float, default=0.60)
    parser.add_argument("--input-root", action="append", default=[])
    parser.add_argument(
        "--once",
        action="store_true",
        help="run one cycle and exit; used by tests and cron",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    paths = KernelPaths.of(
        args.state_root,
        args.plugins_root,
        args.admissions_root,
        capacity_cpu_cores=args.capacity_cpu_cores,
        capacity_memory_bytes=args.capacity_memory_bytes,
        platform_fraction=args.platform_fraction,
        input_roots=tuple(args.input_root),
    )
    worker_id = args.worker_id or f"{socket.gethostname()}-{os.getpid()}"
    parts = build_kernel_parts(paths, worker_id=worker_id)
    worker = RuntimeWorker(
        parts.store,
        parts.runtime,
        idle_seconds=args.idle_seconds,
        batch=args.batch,
    )

    if args.once:
        # JSON, because the caller is a script or an operator, not Python.
        print(json.dumps(worker.cycle().to_dict(), sort_keys=True))
        parts.store.close()
        return 0

    stop = threading.Event()

    def request_stop(signum, _frame):
        # A worker stops between attempts, never mid-attempt: an interrupted
        # attempt becomes a reclaimable lease, and killing the process would
        # orphan whatever it spawned.
        LOGGER.info("signal %s received; finishing the current cycle", signum)
        stop.set()

    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), request_stop)

    worker.serve_forever(stop)
    parts.store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
