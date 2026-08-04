from __future__ import annotations

import argparse
import os
from pathlib import Path

from openroad_platform_contracts import RunRequest, RunStage, RunStatus

from .orfs_runner import ORFSRunner


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run an RTL design through native ORFS")
    result.add_argument("rtl", type=Path)
    result.add_argument("--top")
    result.add_argument("--clock")
    result.add_argument("--period", type=float, default=10.0)
    result.add_argument("--platform", default="nangate45")
    result.add_argument("--stage", choices=[item.value for item in RunStage], default="finish")
    result.add_argument("--core-utilization", type=float, default=10.0)
    result.add_argument("--place-density", type=float, default=0.45)
    result.add_argument("--timeout", type=int, default=3600)
    result.add_argument("--work-root", type=Path, default=Path("var/runs"))
    result.add_argument("--orfs-root", type=Path,
                        default=Path(os.environ.get("ORFS_ROOT", Path.home() / "OpenROAD-flow-scripts")))
    result.add_argument("--openroad-bin", type=Path, default=None)
    result.add_argument("--yosys-bin", type=Path, default=None)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    request = RunRequest(
        rtl_path=str(args.rtl.expanduser().resolve()),
        top=args.top,
        clock=args.clock,
        clock_period_ns=args.period,
        platform=args.platform,
        target_stage=RunStage(args.stage),
        core_utilization_pct=args.core_utilization,
        place_density=args.place_density,
        stage_timeout_seconds=args.timeout,
    )
    runner = ORFSRunner(
        orfs_root=args.orfs_root,
        work_root=args.work_root,
        openroad_bin=args.openroad_bin,
        yosys_bin=args.yosys_bin,
    )
    plan = runner.prepare(request)
    print(f"run_id={plan.run_id} design={plan.design} workdir={plan.workdir}")
    result = runner.run(plan, on_line=lambda line: print(line, end=""))
    print(result.to_json())
    return 0 if result.status == RunStatus.SUCCEEDED else 2


if __name__ == "__main__":
    raise SystemExit(main())
