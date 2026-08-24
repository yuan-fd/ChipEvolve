#!/usr/bin/env python3
"""Audit the v2 Agent architecture against real closed-loop traces."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path


VOCABULARY = {"map", "semantic", "experiment", "hypothesis", "implement",
              "validate", "review", "memory", "diagnosis"}
CORE = {"map", "semantic", "experiment", "hypothesis", "implement",
        "validate", "review", "memory"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for raw in args.experiment:
        root = raw.expanduser().resolve()
        report = json.loads((root / "report.json").read_text(encoding="utf-8"))
        audit = json.loads((root / "protocol-audit.json").read_text(encoding="utf-8"))
        state = report["checkpoint"]["state"]
        events = state.get("agent_events") or []
        phases = [item.get("phase") for item in events]
        evidence_safe = all(
            bool(item.get("run_ids")) and item.get("execution_allowed") is False
            for item in events if item.get("phase") == "validate")
        proposal_safe = all(
            item.get("execution_allowed") is False and item.get("proposal_id")
            for item in events if item.get("phase") == "hypothesis")
        implementation_attributed = all(
            item.get("execution_allowed") is True
            and "Runtime" in str(item.get("authority") or "")
            and item.get("proposal_id") and item.get("parameters")
            and item.get("evidence_refs")
            for item in events if item.get("phase") == "implement")
        memory_attributed = all(
            item.get("run_ids") and item.get("execution_allowed") is False
            for item in events if item.get("phase") == "memory" and "outcome" in item)
        rows.append({
            "design": report["design"], "pipeline_id": report["pipeline_id"],
            "status": state["status"], "event_count": len(events),
            "phase_counts": dict(sorted(Counter(phases).items())),
            "checks": {
                "stable_phase_vocabulary": set(phases) <= VOCABULARY,
                "all_core_phases_observed": CORE <= set(phases),
                "validation_uses_runtime_run_ids": evidence_safe,
                "hypotheses_are_non_executable": proposal_safe,
                "implementation_owned_by_runtime": implementation_attributed,
                "memory_outcomes_are_runtime_attributed": memory_attributed,
                "protocol_audit_passed": audit.get("status") == "passed",
                "checkpoint_revision_positive": report["checkpoint"].get("revision", 0) > 0,
            },
        })
    suite_checks = {
        "four_design_real_trace_suite": {row["design"] for row in rows}
            == {"gcd", "fifo", "uart_tx", "ibex_alu"},
        "all_trace_checks_passed": all(all(row["checks"].values()) for row in rows),
        "both_completion_and_diagnosis_boundaries_observed":
            {row["status"] for row in rows} >= {"completed", "diagnosis_required"},
    }
    result = {
        "schema_version": 1, "kind": "v2_agent_architecture_audit",
        "status": "passed" if all(suite_checks.values()) else "failed",
        "checks": suite_checks, "design_rows": sorted(rows, key=lambda item: item["design"]),
        "test_interventions_required": [
            "baseline replica submission interrupted after one child; resume creates only missing suffix",
            "candidate replica submission interrupted after one child; resume preserves proposal identity",
            "terminal resume is idempotent and creates no new experiment",
            "clock injected into BO search space is rejected",
            "browser-supplied transition/seed/search controls are rejected",
        ],
        "claim_boundary": (
            "Real traces prove orchestration, evidence attribution and execution separation. "
            "Injected-interruption tests prove checkpoint value; this does not claim that "
            "multi-agent decomposition alone improves QoR."),
    }
    destination = args.output.expanduser().resolve(); destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2),
                           encoding="utf-8")
    print(json.dumps({"output": str(destination), "status": result["status"],
                      "designs": len(rows), "statuses": sorted(
                          {row["status"] for row in rows})}, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
