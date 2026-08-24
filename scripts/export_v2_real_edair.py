#!/usr/bin/env python3
"""Export EDAIR from a completed real Runtime run and audit fidelity links."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app import ApiState  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--focus", choices=("diagnosis", "timing", "physical",
                                             "connectivity", "qor"),
                        default="diagnosis")
    parser.add_argument("--orfs-root", type=Path,
                        default=Path("/share/home/yuanwenjie/OpenROAD-flow-scripts"))
    args = parser.parse_args()
    root = args.experiment.expanduser().resolve()
    if not (root / "runtime.db").is_file():
        raise SystemExit("experiment has no runtime.db")
    state = ApiState(
        root / "platform.db", root / "uploads", args.orfs_root,
        design_root=root / "designs", legacy_root=root / "legacy",
        runtime_db_path=root / "runtime.db",
        optimization_db_path=root / "optimization.db", load_taiwei_plugin=False,
    )
    runs = state.list_runtime_runs(limit=500)["runs"]
    run_id = args.run_id or next(
        (item["run_id"] for item in reversed(runs) if item["status"] == "succeeded"), None)
    if run_id is None:
        raise SystemExit("experiment has no succeeded Runtime run")
    exported = state.runtime_edair(run_id, focus=args.focus)
    edair = exported["edair"]
    raw = edair.get("raw_artifacts") or []
    fidelity = edair.get("fidelity_manifest") or {}
    report = {
        "schema_version": 1,
        "kind": "v2_real_edair_acceptance",
        "run_id": run_id,
        "source_experiment": str(root),
        "edair": edair,
        "agent_view": exported["agent_view"],
        "evidence_packet": exported["evidence_packet"],
        "acceptance": {
            "raw_artifacts_registered": len(raw),
            "all_raw_artifacts_have_sha256": bool(raw) and all(
                len(str(item.get("sha256") or "")) == 64 for item in raw),
            "detail_recovery_available": bool(fidelity.get("detail_recovery")),
            "losses_declared": "loss_manifest" in edair or bool(fidelity),
            "timing_available": edair.get("timing") is not None,
            "physical_available": edair.get("physical") is not None,
            "design_available": edair.get("design") is not None,
            "design_instances": len((edair.get("design") or {}).get("instances", [])),
            "timing_paths": len((edair.get("timing") or {}).get("paths", [])),
            "physical_instances": len((edair.get("physical") or {}).get("instances", [])),
            "logical_nets": len((edair.get("physical") or {}).get("nets", [])),
            "agent_facts": len(exported["agent_view"].get("facts", [])),
            "packet_facts": len(exported["evidence_packet"].get("facts", [])),
        },
        "claim_boundary": (
            "EDAIR is a provenance-linked projection, not a replacement for raw EDA files; "
            "missing parser coverage remains explicit and can be recovered through bounded "
            "SHA-checked artifact excerpts."
        ),
    }
    destination = root / f"edair-{run_id}.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2),
                         encoding="utf-8")
    temporary.replace(destination)
    print(json.dumps({"output": str(destination), **report["acceptance"]},
                     ensure_ascii=False))
    required = report["acceptance"]
    return 0 if (
        required["all_raw_artifacts_have_sha256"]
        and required["detail_recovery_available"]
        and required["losses_declared"]
        and required["design_available"] and required["design_instances"] > 0
        and required["timing_available"] and required["timing_paths"] > 0
        and required["physical_available"] and required["physical_instances"] > 0
        and required["logical_nets"] > 0 and required["packet_facts"] > 0
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
