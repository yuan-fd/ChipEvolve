#!/usr/bin/env python3
"""Analyze repeated RTLScout attempts and same-context hidden RTL references."""
from __future__ import annotations

import argparse
from collections import Counter
from itertools import chain
import json
import math
from pathlib import Path
import statistics
import sys


ROOT = Path(__file__).resolve().parents[1]
FULL_CANDIDATE_GATES = ("compile_lint", "simulation", "mutation_quality", "ppa")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app import ApiState  # noqa: E402


def _pass_at_k(n: int, successes: int, k: int) -> float:
    if successes <= 0:
        return 0.0
    if n - successes < k:
        return 1.0
    return 1.0 - math.comb(n - successes, k) / math.comb(n, k)


def _kpi(root: Path, run_id: str) -> dict:
    state = ApiState(root / "platform.db", root / "uploads",
                     Path("/share/home/yuanwenjie/OpenROAD-flow-scripts"),
                     design_root=root / "designs", legacy_root=root / "legacy",
                     runtime_db_path=root / "runtime.db",
                     optimization_db_path=root / "optimization.db", load_taiwei_plugin=False)
    view = state.get_runtime_run(run_id)
    return ((view.get("analysis_report") or {}).get("report") or {}).get("kpi") or {}


def _full_candidate_gate_result(checks: list[dict], candidate_id: str | None) -> dict:
    """Judge an authored candidate at the product boundary, not inside RTLScout.

    RTLScout's own evaluator only covers its local lint/simulation loop.  A
    paper-level first-candidate success additionally requires the independently
    frozen testbench, mutation-quality gate, and the full ORFS PPA/GDS run.
    """
    selected = [item for item in checks if item.get("candidate_id") == candidate_id]
    outcomes = {kind: next((item.get("status") for item in reversed(selected)
                            if item.get("check_kind") == kind), "missing")
                for kind in FULL_CANDIDATE_GATES}
    return {"passed": all(outcomes[kind] == "passed" for kind in FULL_CANDIDATE_GATES),
            "outcomes": outcomes}


def _attempt(root: Path, design: str, index: int) -> dict:
    report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    pipeline = report.get("pipeline") or {}; lineage = report.get("lineage") or {}
    candidates = lineage.get("candidates") or []; checks = lineage.get("checks") or []
    final_id = pipeline.get("candidate_id")
    final = next((item for item in candidates if item.get("candidate_id") == final_id), {})
    histories = []
    for path in root.rglob("outputs/candidate_history.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        histories.append({"path": str(path.relative_to(root)), **value})
    evaluations = list(chain.from_iterable(item.get("candidates") or [] for item in histories))
    first = evaluations[0] if evaluations else None
    rtlscout_steps = [item for item in pipeline.get("steps") or []
                      if item.get("role") == "rtlscout"]
    first_candidate_id = (((rtlscout_steps[0].get("collection") or {}).get("candidate_id"))
                          if rtlscout_steps else
                          (candidates[0].get("candidate_id") if candidates else None))
    first_gate = _full_candidate_gate_result(checks, first_candidate_id)
    ppa_check = next((item for item in reversed(checks)
                      if item.get("candidate_id") == final_id and item.get("check_kind") == "ppa"), None)
    run_id = ((ppa_check or {}).get("detail") or {}).get("run_id")
    kpi = _kpi(root, run_id) if run_id and report.get("status") == "passed" else {}
    verification = next((item for item in lineage.get("verification_packages") or []
                         if item.get("verification_id") == final.get("verification_id")), {})
    refs = verification.get("simulation_oracle_refs") or []
    mutation_check = next((item for item in reversed(checks)
                           if item.get("candidate_id") == final_id
                           and item.get("check_kind") == "mutation_quality"), None)
    mutation = ((mutation_check or {}).get("detail") or {}).get("mutation") or {}
    return {
        "design": design, "attempt": index, "status": report.get("status"),
        "pipeline_status": pipeline.get("status"), "passed": report.get("status") == "passed",
        "spec_id": (report.get("specir") or {}).get("spec_id"),
        "rtl_sha256": (final.get("provenance") or {}).get("rtl_sha256"),
        "testbench_sha256": refs[0].rsplit(":", 1)[-1] if refs else None,
        "candidate_evaluations": len(evaluations),
        "first_authored_candidate_id": first_candidate_id,
        "first_rtlscout_local_evaluator_passed": bool(first and first.get("passed") is True),
        "first_authored_candidate_gate_outcomes": first_gate["outcomes"],
        "first_authored_candidate_passed": first_gate["passed"],
        "iterative_rescue": bool(report.get("status") == "passed"
                                  and not first_gate["passed"]
                                  and final_id != first_candidate_id),
        "mutation": {key: mutation.get(key) for key in ("generated_count", "executable_count",
                     "killed_count", "survived_count", "invalid_count", "mutation_score")},
        "rtl_revisions": pipeline.get("rtl_revision"),
        "revision_history": pipeline.get("revision_history") or [],
        "orfs_run_id": run_id, "kpi": kpi,
        "failure": report.get("failure"), "candidate_histories": histories,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(); matrix = args.matrix.expanduser().resolve()
    ledger = json.loads((matrix / "matrix-report.json").read_text(encoding="utf-8"))
    references = json.loads((args.references.expanduser().resolve() / "report.json").read_text(encoding="utf-8"))
    reference_by_design = {row["design"]: row for row in references["design_rows"]}
    rows = [_attempt(Path(item["destination"]), item["design"], item["attempt"])
            for item in ledger["attempts"]]
    designs = []
    for design in sorted({row["design"] for row in rows}):
        selected = [row for row in rows if row["design"] == design]
        passed = [row for row in selected if row["passed"]]
        reference = reference_by_design[design]["metrics"]
        ppa = {}
        for metric in ("area_um2", "setup_wns_ns", "power_W"):
            values = [float(row["kpi"][metric]) for row in passed
                      if isinstance(row["kpi"].get(metric), (int, float))]
            golden = reference[metric]["median"]
            ppa[metric] = {"generated_values": values,
                           "generated_median": statistics.median(values) if values else None,
                           "golden_median": golden,
                           "relative_generated_minus_golden": (
                               (statistics.median(values) - golden) / max(abs(golden), 1e-12)
                               if values and isinstance(golden, (int, float)) else None)}
        successes = len(passed); n = len(selected)
        designs.append({"design": design, "attempts": n, "successes": successes,
                        "pass_rate": successes / n,
                        "pass_at_k": {str(k): _pass_at_k(n, successes, k)
                                      for k in range(1, n + 1)},
                        "first_candidate_passes": sum(x["first_authored_candidate_passed"] for x in selected),
                        "iterative_rescues": sum(x["iterative_rescue"] for x in selected),
                        "unique_rtl_hashes": len({x["rtl_sha256"] for x in passed if x["rtl_sha256"]}),
                        "unique_testbench_hashes": len({x["testbench_sha256"] for x in selected if x["testbench_sha256"]}),
                        "mutation_blocked_revisions": sum(
                            sum(item.get("failed_stage") == "mutation_quality"
                                for item in x["revision_history"]) for x in selected),
                        "ppa_vs_hidden_golden": ppa})
    total = len(rows); success = sum(row["passed"] for row in rows)
    result = {"schema_version": 1, "kind": "v2_paper_rtl_frozen_analysis",
              "protocol_id": ledger["protocol_id"], "status": "complete",
              "attempts": total, "successes": success, "full_chain_pass_rate": success / total,
              "first_authored_candidate_pass_rate": sum(x["first_authored_candidate_passed"] for x in rows) / total,
              "iterative_rescue_count": sum(x["iterative_rescue"] for x in rows),
              "failure_types": dict(Counter((x.get("failure") or {}).get("type", "pipeline_failure")
                                            for x in rows if not x["passed"])),
              "design_rows": designs, "attempt_rows": rows,
              "claim_boundary": "Fixed four-design, five-attempt suite with hidden same-context golden references; not arbitrary-spec generalization or proof of functional equivalence to golden RTL."}
    output = args.output.expanduser().resolve(); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "successes": success, "attempts": total}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
