#!/usr/bin/env python3
"""Repeat all three sealed platform demo chains in fresh Runtime workspaces."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(*arguments: str) -> None:
    subprocess.run([sys.executable, *arguments], cwd=ROOT, check=True)


def _artifact_path(view: dict, kind: str) -> Path:
    for stage in view["stages"]:
        for attempt in stage["attempts"]:
            for artifact in attempt["artifacts"]:
                if artifact["kind"] == kind:
                    return Path(attempt["workspace"]) / artifact["store_key"]
    raise KeyError(kind)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--orfs-root", type=Path, default=Path.home() / "OpenROAD-flow-scripts")
    parser.add_argument("--openroad-bin", type=Path, default=Path.home() / "bin/openroad")
    parser.add_argument("--yosys-bin", type=Path, default=Path.home() / "bin/yosys")
    parser.add_argument("--klayout-bin", type=Path, default=Path.home() / "bin/klayout")
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--num-cores", type=int, default=8)
    args = parser.parse_args()
    output = args.output_root.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(output)
    output.mkdir(parents=True, exist_ok=True)
    live = Path("/tmp") / f"openroad-platform-demo-{uuid.uuid4().hex}"
    live.mkdir(parents=True)

    p4_out = output / "01-rtlscout-to-2d"
    _run("scripts/run_p4_acceptance.py", "--output-root", str(p4_out),
         "--runtime-db", str(live / "rtlscout-2d.db"),
         "--orfs-root", str(args.orfs_root), "--openroad-bin", str(args.openroad_bin),
         "--yosys-bin", str(args.yosys_bin), "--klayout-bin", str(args.klayout_bin),
         "--timeout", str(args.timeout))
    p4 = json.loads((p4_out / "acceptance_summary.json").read_text(encoding="utf-8"))
    rtl = _artifact_path(p4["rtl_run"], "rtl")

    p5_out = output / "02-agenticpd-campaign"
    _run("scripts/run_p5_acceptance.py", "--output-root", str(p5_out),
         "--runtime-db", str(live / "agenticpd.db"),
         "--campaign-db", str(live / "agenticpd-campaign.db"), "--rtl", str(rtl),
         "--orfs-root", str(args.orfs_root), "--openroad-bin", str(args.openroad_bin),
         "--yosys-bin", str(args.yosys_bin), "--klayout-bin", str(args.klayout_bin),
         "--timeout", str(args.timeout), "--max-parallel", "1")

    p8_out = output / "03-taiwei-3d"
    _run("scripts/run_p8_real_acceptance.py", "--output-root", str(p8_out),
         "--timeout-seconds", str(max(args.timeout, 21600)),
         "--num-cores", str(args.num_cores))
    p8 = json.loads((p8_out / "acceptance_summary.json").read_text(encoding="utf-8"))
    p8_db = json.loads((p8_out / "runtime_database.json").read_text(encoding="utf-8"))

    resilience_out = output / "04-resilience"
    _run("scripts/run_p8_real_resilience.py", "--output-root", str(resilience_out),
         "--num-cores", str(min(args.num_cores, 4)))
    _run("scripts/verify_p8_real_api.py", "--runtime-db", p8_db["live_path"],
         "--campaign-db", str(live / "taiwei-campaign.db"),
         "--run-id", p8["run_id"], "--output", str(p8_out / "api_web_verification.json"))
    backup_verification = p8_out / "backup_restore_verification.json"
    _run("scripts/verify_runtime_backup.py", "--snapshot",
         str(p8_out / "runtime.db.snapshot"), "--run-id", p8["run_id"],
         "--output", str(backup_verification))
    _run("scripts/verify_platform_demo.py", "--p4-evidence",
         str(p4_out / "acceptance_summary.json"), "--p5-evidence",
         str(p5_out / "acceptance_summary.json"), "--p8-evidence",
         str(p8_out / "acceptance_summary.json"), "--p8-api-evidence",
         str(p8_out / "api_web_verification.json"), "--resilience-evidence",
         str(resilience_out / "resilience_summary.json"), "--output",
         str(output / "platform_demo_verification.json"))
    summary = {
        "schema_version": 1, "accepted": True, "live_state_root": str(live),
        "chains": [str(p4_out), str(p5_out), str(p8_out)],
        "resilience": str(resilience_out),
        "backup_restore": str(backup_verification),
        "verification": str(output / "platform_demo_verification.json"),
    }
    (output / "demo_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
