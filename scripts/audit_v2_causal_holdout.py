#!/usr/bin/env python3
"""Independently audit a persisted v2 causal/holdout experiment."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app import ApiState  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--orfs-root", type=Path,
                        default=Path("/share/home/yuanwenjie/OpenROAD-flow-scripts"))
    args = parser.parse_args()
    root = args.experiment.expanduser().resolve()
    source = json.loads((root / "report.json").read_text(encoding="utf-8"))
    state = ApiState(
        root / "platform.db", root / "uploads", args.orfs_root,
        design_root=root / "designs", legacy_root=root / "legacy",
        runtime_db_path=root / "runtime.db",
        optimization_db_path=root / "optimization.db", load_taiwei_plugin=False)
    source_ids = list(source.get("source_run_ids") or [])
    holdout_ids = list(source.get("holdout_run_ids") or [])
    rows = []
    for role, run_ids in (("source", source_ids), ("holdout", holdout_ids)):
        for run_id in run_ids:
            run = state.runtime_store.get_run(run_id)
            view = state.get_runtime_run(run_id)
            envelope = view.get("analysis_report") or {}
            report = envelope.get("report") or {}
            kpi = report.get("kpi") or {}
            rows.append({
                "role": role, "run_id": run_id, "status": run.status.value,
                "created_at": run.created_at,
                "setup_wns_ns": kpi.get("setup_wns_ns"),
                "drc_errors": kpi.get("drc_errors"),
                "parameters": dict(run.task_spec.parameters),
                "analysis_sha256": envelope.get("source_sha256"),
            })
    hypothesis = source.get("hypothesis") or {}
    with sqlite3.connect(root / "hypothesis-ledger.db") as connection:
        ledger_rows = connection.execute(
            "SELECT payload_json,created_at FROM hypothesis_events_v1 "
            "WHERE hypothesis_id=? ORDER BY created_at",
            (hypothesis.get("hypothesis_id"),)).fetchall()
    ledger = [{"record": json.loads(payload), "created_at": created}
              for payload, created in ledger_rows]
    first_holdout = min((row["created_at"] for row in rows if row["role"] == "holdout"),
                        default="")
    preregistered = bool(ledger and first_holdout and
                         datetime.fromisoformat(ledger[0]["created_at"])
                         < datetime.fromisoformat(first_holdout))
    validation = source.get("validation") or {}
    checks = {
        "exact_repeated_2x2_run_count": len(source_ids) == 12 and len(holdout_ids) == 12,
        "all_runs_terminal_success": bool(rows) and all(row["status"] == "succeeded" for row in rows),
        "all_runs_have_attributed_analysis": all(
            isinstance(row["analysis_sha256"], str) and len(row["analysis_sha256"]) == 64
            for row in rows),
        "timing_constraint_passed": all(
            isinstance(row["setup_wns_ns"], (int, float)) and row["setup_wns_ns"] >= 0
            for row in rows),
        "drc_constraint_passed": all(row["drc_errors"] == 0 for row in rows),
        "source_causal_eligible": source.get("source_report", {}).get("causal_eligible") is True,
        "holdout_causal_eligible": validation.get("holdout", {}).get("causal_eligible") is True,
        "cross_design_judgement_recorded": validation.get("validation", {}).get("outcome")
            in {"validated", "rejected"},
        "hypothesis_precedes_holdout": preregistered,
        "append_only_learning_history": len(ledger) >= 3,
        "no_refuted_rule_is_action_eligible": not bool(
            (validation.get("knowledge_card") or {}).get("action_eligible")),
    }
    audit = {
        "schema_version": 1, "kind": "v2_causal_holdout_audit",
        "status": "passed" if all(checks.values()) else "failed",
        "checks": checks, "run_rows": rows,
        "ledger_history": ledger,
        "source_interaction": source.get("source_report", {}).get("interaction_effect"),
        "holdout_interaction": validation.get("holdout", {}).get("interaction_effect"),
        "holdout_outcome": validation.get("validation", {}).get("outcome"),
        "claim_boundary": (
            "This audit establishes a controlled two-design transfer test, not a universal "
            "causal law or statistical-significance claim."),
    }
    destination = root / "causal-audit.json"
    destination.write_text(json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2),
                           encoding="utf-8")
    print(json.dumps({"output": str(destination), "status": audit["status"],
                      "checks": checks}, ensure_ascii=False))
    return 0 if audit["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
