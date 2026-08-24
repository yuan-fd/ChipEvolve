#!/usr/bin/env python3
"""Compare summary-only observation with provenance-linked EDAIR on real runs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--edair", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for raw in args.edair:
        path = raw.expanduser().resolve(); record = json.loads(path.read_text(encoding="utf-8"))
        edair = record["edair"]; run = edair["run"]; physical = edair.get("physical") or {}
        timing = edair.get("timing") or {}; design = edair.get("design") or {}
        summary_kpi = (run.get("physical_report") or {}).get("kpi") or {}
        packet = record["evidence_packet"]
        row = {
            "run_id": record["run_id"],
            "design_id": run.get("run", {}).get("design_id"),
            "summary_only": {"numeric_kpi_count": sum(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                for value in summary_kpi.values()),
                "timing_path_objects": 0, "logical_net_objects": 0,
                "logical_instance_objects": 0, "physical_instance_objects": 0,
                "raw_artifact_directory": 0},
            "edair": {
                "numeric_kpi_count": sum(isinstance(value, (int, float))
                                         and not isinstance(value, bool)
                                         for value in summary_kpi.values()),
                "timing_path_objects": len(timing.get("paths", [])),
                "logical_net_objects": len(physical.get("nets", [])),
                "logical_instance_objects": len(design.get("instances", [])),
                "physical_instance_objects": len(physical.get("instances", [])),
                "raw_artifact_directory": len(edair.get("raw_artifacts", [])),
                "agent_packet_facts": len(packet.get("facts", [])),
                "loss_manifest": packet.get("loss_manifest"),
                "fingerprint": edair.get("fingerprint"),
            },
        }
        row["checks"] = {
            "raw_artifacts_sha_attributed": all(
                len(str(item.get("sha256") or "")) == 64
                for item in edair.get("raw_artifacts", [])),
            "timing_paths_recovered": row["edair"]["timing_path_objects"] > 0,
            "logical_connectivity_recovered": row["edair"]["logical_net_objects"] > 0,
            "logical_instances_recovered": row["edair"]["logical_instance_objects"] > 0,
            "physical_instances_recovered": row["edair"]["physical_instance_objects"] > 0,
            "losses_declared": isinstance(packet.get("loss_manifest"), dict),
            "bounded_agent_packet": 0 < row["edair"]["agent_packet_facts"] <= 48,
        }
        rows.append(row)
    checks = {
        "four_real_design_views": len(rows) == 4,
        "all_fidelity_checks_passed": all(all(row["checks"].values()) for row in rows),
        "edair_strictly_adds_structural_detail": all(
            sum(row["edair"][key] for key in (
                "timing_path_objects", "logical_net_objects",
                "logical_instance_objects", "physical_instance_objects")) > 0
            for row in rows),
    }
    result = {
        "schema_version": 1, "kind": "v2_edair_summary_ablation",
        "status": "passed" if all(checks.values()) else "failed", "checks": checks,
        "design_rows": rows,
        "totals": {
            key: sum(row["edair"][key] for row in rows) for key in (
                "timing_path_objects", "logical_net_objects", "logical_instance_objects",
                "physical_instance_objects", "raw_artifact_directory", "agent_packet_facts")},
        "claim_boundary": (
            "EDAIR demonstrably preserves more queryable structure and provenance than a KPI "
            "summary. This fidelity ablation does not by itself prove higher downstream QoR."),
    }
    destination = args.output.expanduser().resolve(); destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2),
                           encoding="utf-8")
    print(json.dumps({"output": str(destination), "status": result["status"],
                      "totals": result["totals"]}, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
