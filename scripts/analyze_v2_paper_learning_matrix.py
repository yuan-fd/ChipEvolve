#!/usr/bin/env python3
"""Summarize the frozen retrieval-only versus causal-admission learning ablation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.matrix.expanduser().resolve()
    matrix = json.loads((root / "matrix-report.json").read_text(encoding="utf-8"))
    rows = []
    for source_row in matrix["pairs"]:
        row = dict(source_row); report_path = Path(row["destination"]) / "report.json"
        if report_path.is_file():
            detail = json.loads(report_path.read_text(encoding="utf-8"))
            row["run_count"] = (len(detail.get("source_run_ids") or [])
                                + len(detail.get("holdout_run_ids") or []))
        else:
            row["run_count"] = 0
        rows.append(row)
    rejected = [row for row in rows if row.get("outcome") == "rejected"]
    validated = [row for row in rows if row.get("outcome") == "validated"]
    ineligible = [row for row in rows if row.get("outcome") not in {"validated", "rejected"}]
    result = {
        "schema_version": 1, "kind": "v2_paper_learning_frozen_analysis",
        "protocol_id": matrix["protocol_id"], "protocol_sha256": matrix["protocol_sha256"],
        "status": "passed" if not ineligible and matrix["status"] == "passed" else "failed",
        "run_count": sum(row["run_count"] for row in rows),
        "ordered_pair_count": len(rows), "validated_pair_count": len(validated),
        "rejected_pair_count": len(rejected), "ineligible_pair_count": len(ineligible),
        "arms": {
            "retrieval_only_counterfactual": {
                "local_rules_admitted": len(validated) + len(rejected),
                "false_transfer_rules_admitted": len(rejected),
                "rule_precision_on_holdout": (len(validated) / (len(validated) + len(rejected))
                                                if validated or rejected else None),
            },
            "causal_holdout_gate": {
                "bounded_rules_admitted": len(validated),
                "contradicted_rules_rejected": len(rejected),
                "false_transfer_rules_admitted": 0,
                "false_transfer_reduction": len(rejected),
            },
        },
        "pairs": rows,
        "claim_boundary": "All results concern the registered four designs, two parameters, one metric and pinned ORFS/Nangate45 context; this proves safer knowledge admission, not autonomous downstream QoR gain.",
    }
    output = args.output.expanduser().resolve(); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "status": result["status"],
                      "false_transfers_prevented": len(rejected)}, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
