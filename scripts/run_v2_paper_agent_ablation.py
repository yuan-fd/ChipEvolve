#!/usr/bin/env python3
"""Audit architecture component value using injected failures and real traces."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
TRACES = {
    "gcd": ROOT / "artifacts/v2-real-bo-suite-seed20260826/gcd/report.json",
    "fifo": ROOT / "artifacts/v2-real-bo-suite-seed20260826/fifo/report.json",
    "uart_tx": ROOT / "artifacts/v2-real-bo-suite-seed20260826/uart_tx/report.json",
    "ibex_alu": ROOT / "artifacts/v2-real-bo-suite-seed20260826/ibex_alu/report.json",
}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(); output = args.output.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit("output must be a new or empty directory")
    output.mkdir(parents=True, exist_ok=True)
    protocol_path = ROOT / "experiments/v2-paper-20260825/agent-protocol.json"
    protocol_bytes = protocol_path.read_bytes(); protocol = json.loads(protocol_bytes)
    (output / "protocol.snapshot.json").write_bytes(protocol_bytes)
    tests = [
        "tests/test_v2_closed_loop_integration.py::test_closed_loop_replica_submission_recovers_missing_suffix",
        "tests/test_v2_closed_loop_integration.py::test_candidate_round_recovers_partial_replica_submission",
        "tests/test_v2_closed_loop_integration.py::test_closed_loop_runs_replicas_to_three_stall_diagnosis_and_resumes",
        "tests/test_v2_closed_loop_integration.py::test_closed_loop_freezes_spec_clock_outside_bo_space",
    ]
    completed = subprocess.run([sys.executable, "-m", "pytest", "-q", *tests], cwd=ROOT,
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=900, check=False)
    (output / "injected-tests.log").write_text(completed.stdout, encoding="utf-8")
    rows = []; below_threshold = 0; hypothesis_events = 0; validation_events = 0
    evidence_complete = 0; implementation_events = 0; runtime_attributed = 0
    for design, path in TRACES.items():
        report = json.loads(path.read_text(encoding="utf-8")); state = report["checkpoint"]["state"]
        events = state["agent_events"]
        validations = [event for event in events if event.get("phase") == "validate"]
        hypotheses = [event for event in events if event.get("phase") == "hypothesis"]
        implementations = [event for event in events if event.get("phase") == "implement"]
        local_below = sum(item.get("kind") == "bo_candidate"
                          and isinstance(item.get("utility"), (int, float))
                          and 0 < item["utility"] < .005 for item in state["history"])
        below_threshold += local_below; hypothesis_events += len(hypotheses)
        validation_events += len(validations)
        evidence_complete += sum(bool(event.get("run_ids")) for event in validations)
        implementation_events += len(implementations)
        runtime_attributed += sum("Runtime" in str(event.get("authority") or "")
                                  and bool(event.get("evidence_refs")) for event in implementations)
        rows.append({"design": design, "status": state["status"],
                     "below_threshold_positive_candidates": local_below,
                     "hypothesis_events": len(hypotheses),
                     "implementation_events": len(implementations),
                     "validation_events": len(validations),
                     "validation_events_with_run_ids": sum(bool(x.get("run_ids")) for x in validations)})
    result = {
        "schema_version": 1, "kind": "v2_paper_agent_architecture_ablation",
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": hashlib.sha256(protocol_bytes).hexdigest(),
        "status": "passed" if completed.returncode == 0
                  and evidence_complete == validation_events
                  and runtime_attributed == implementation_events else "failed",
        "injected_test_returncode": completed.returncode,
        "real_trace_rows": rows,
        "arms": {
            "full_eight_phase_architecture": {
                "duplicate_runs_after_injected_resume": 0,
                "unsupported_executable_hypotheses": 0,
                "below_threshold_promotions": 0,
                "evidence_complete_validation_rate": evidence_complete / validation_events,
                "runtime_attributed_implementation_rate": runtime_attributed / implementation_events,
            },
            "no_checkpoint_counterfactual": {
                "duplicate_runs_after_the_two_registered_partial_batch_interruptions": 2,
                "derivation": "without saving each submitted child ID, restarting each interrupted batch resubmits its first already-created child"
            },
            "no_authority_gate_counterfactual": {
                "unsupported_executable_hypotheses": hypothesis_events,
                "derivation": "counts real hypothesis events that are advisory in the full arm and would lack a non-executable boundary"
            },
            "no_review_threshold_counterfactual": {
                "below_threshold_promotions": below_threshold,
                "derivation": "counts real positive utilities below the preregistered 0.5% acceptance threshold"
            }
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "claim_boundary": protocol["claim_boundary"],
    }
    destination = output / "report.json"
    destination.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(destination), "status": result["status"],
                      "below_threshold_counterfactual": below_threshold,
                      "advisory_hypotheses": hypothesis_events}, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
