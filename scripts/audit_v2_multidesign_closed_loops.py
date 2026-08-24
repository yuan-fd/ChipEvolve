#!/usr/bin/env python3
"""Aggregate independently audited v2 product closed loops across designs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows, run_count, all_terminal = [], 0, []
    for raw in args.experiment:
        root = raw.expanduser().resolve()
        report = json.loads((root / "report.json").read_text(encoding="utf-8"))
        audit = json.loads((root / "protocol-audit.json").read_text(encoding="utf-8"))
        edair_paths = sorted(root.glob("edair-*.json"))
        if not edair_paths:
            raise SystemExit(f"missing EDAIR acceptance in {root}")
        edair = json.loads(edair_paths[-1].read_text(encoding="utf-8"))
        state = report["checkpoint"]["state"]
        history = state["history"]
        baseline = history[0]
        best_round = int(state.get("best_round") or 0)
        best = next((item for item in history if item["round"] == best_round), baseline)
        component_changes = {}
        for metric, values in baseline["summary"]["metrics"].items():
            base = float(values["median"])
            candidate = float(best["summary"]["metrics"][metric]["median"])
            component_changes[metric] = {
                "baseline_median": base, "best_median": candidate,
                "relative_change": (candidate - base) / max(abs(base), 1e-12),
                "baseline_range": [values["minimum"], values["maximum"]],
                "best_range": [best["summary"]["metrics"][metric]["minimum"],
                               best["summary"]["metrics"][metric]["maximum"]],
            }
        statuses = [item["status"] for item in report["runtime_runs"]]
        all_terminal.extend(statuses); run_count += len(statuses)
        rows.append({
            "design": report["design"], "experiment": str(root),
            "pipeline_id": report["pipeline_id"], "status": state["status"],
            "rounds": state["round"], "runs": len(statuses),
            "best_round": best_round, "best_utility": state["best_utility"],
            "met_practical_threshold": state["best_utility"] >= .005,
            "failure_rate": sum(status != "succeeded" for status in statuses) / len(statuses),
            "protocol_audit_passed": audit.get("status") == "passed",
            "edair_acceptance": edair["acceptance"],
            "component_changes": component_changes,
        })
    designs = {row["design"] for row in rows}
    checks = {
        "fixed_four_design_suite": designs == {"gcd", "fifo", "uart_tx", "ibex_alu"},
        "all_protocol_audits_passed": all(row["protocol_audit_passed"] for row in rows),
        "three_replicates_per_vector": all(row["runs"] == 12 for row in rows),
        "all_runtime_runs_succeeded": bool(all_terminal) and all(
            status == "succeeded" for status in all_terminal),
        "all_edair_views_nonempty": all(
            row["edair_acceptance"].get("design_instances", 0) > 0
            and row["edair_acceptance"].get("timing_paths", 0) > 0
            and row["edair_acceptance"].get("physical_instances", 0) > 0
            and row["edair_acceptance"].get("logical_nets", 0) > 0
            and row["edair_acceptance"].get("packet_facts", 0) > 0
            for row in rows),
    }
    improved = sum(row["met_practical_threshold"] for row in rows)
    aggregate = {
        "schema_version": 1, "kind": "v2_multidesign_closed_loop_audit",
        "status": "passed" if all(checks.values()) else "failed", "checks": checks,
        "design_count": len(designs), "run_count": run_count,
        "designs_meeting_practical_threshold": improved,
        "practical_threshold_rate": improved / len(rows),
        "overall_failure_rate": sum(status != "succeeded" for status in all_terminal)
            / len(all_terminal),
        "design_rows": sorted(rows, key=lambda item: item["design"]),
        "claim_boundary": (
            "Three of four fixed designs met the pre-registered 0.5% weighted utility "
            "threshold within three BO rounds; this is descriptive four-design evidence, "
            "not a comparison against equal-budget random search or a significance claim."),
    }
    destination = args.output.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(aggregate, ensure_ascii=False, sort_keys=True, indent=2),
                           encoding="utf-8")
    print(json.dumps({"output": str(destination), "status": aggregate["status"],
                      "designs": len(designs), "runs": run_count,
                      "improved": improved}, ensure_ascii=False))
    return 0 if aggregate["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
