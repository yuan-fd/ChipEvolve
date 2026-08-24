#!/usr/bin/env python3
"""Produce a read-only repeated-2x2 report from native ORFS evidence folders."""
from __future__ import annotations

import argparse
import json

from openroad_platform_analysis import factorial_interaction_report, native_orfs_run_view


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workdirs", nargs="+", help="completed native ORFS work directories")
    parser.add_argument("--first", required=True)
    parser.add_argument("--second", required=True)
    parser.add_argument("--metric", required=True)
    parser.add_argument("--allow-failed-evidence", action="store_true",
                        help="include verified failed native runs so the report can fail closed")
    args = parser.parse_args(argv)
    report = factorial_interaction_report(
        [native_orfs_run_view(item, allow_non_success=args.allow_failed_evidence)
         for item in args.workdirs],
        first=args.first, second=args.second, metric=args.metric,
    )
    report["evidence_origin"] = "native-orfs-cli-artifacts"
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("causal_eligible") else 2


if __name__ == "__main__":
    raise SystemExit(main())
