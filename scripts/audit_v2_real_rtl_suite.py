#!/usr/bin/env python3
"""Audit the four natural-language RTLScout-to-GDS acceptance records."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app import ApiState  # noqa: E402


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", type=Path, action="append", required=True)
    parser.add_argument("--failed-experiment", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--orfs-root", type=Path,
                        default=Path("/share/home/yuanwenjie/OpenROAD-flow-scripts"))
    args = parser.parse_args()
    rows = []
    for raw in args.experiment:
        root = raw.expanduser().resolve()
        report = json.loads((root / "report.json").read_text(encoding="utf-8"))
        design = report["design"]; lineage = report["lineage"]; pipeline = report["pipeline"]
        state = ApiState(
            root / "platform.db", root / "uploads", args.orfs_root,
            design_root=root / "designs", legacy_root=root / "legacy",
            runtime_db_path=root / "runtime.db",
            optimization_db_path=root / "optimization.db", load_taiwei_plugin=False)
        checks = lineage["checks"]
        final_candidate = next(item for item in lineage["candidates"]
                               if item["candidate_id"] == pipeline["candidate_id"])
        final_mutation = next(item for item in reversed(checks)
                              if item["candidate_id"] == final_candidate["candidate_id"]
                              and item["check_kind"] == "mutation_quality")
        mutation = final_mutation["detail"]["mutation"]
        oracle_ref = next(item for item in lineage["verification_packages"]
                          if item["verification_id"] == final_candidate["verification_id"])[
                              "simulation_oracle_refs"][0]
        oracle_sha = oracle_ref.rsplit(":", 1)[-1]
        oracle_path = root / "verification-oracles" / f"{oracle_sha}.sv"
        receipt_paths = list((root / "verification-oracles").glob(
            f"{oracle_sha}.*.approval.json"))
        receipt = json.loads(receipt_paths[0].read_text(encoding="utf-8")) if receipt_paths else {}
        rtl_sha = final_candidate["provenance"]["rtl_sha256"]
        rtl_path = root / "rtl-candidates" / f"{rtl_sha}.sv"
        golden = ROOT / "benchmarks" / "v2" / design / "golden.sv"
        orfs_run_id = next(item["detail"]["run_id"] for item in reversed(checks)
                           if item["candidate_id"] == final_candidate["candidate_id"]
                           and item["check_kind"] == "ppa")
        view = state.get_runtime_run(orfs_run_id)
        artifacts = [item for stage in view["stages"] for attempt in stage["attempts"]
                     for item in attempt.get("artifacts", [])]
        gds = next((item for item in artifacts if item.get("kind") == "gds"), None)
        gds_path = None
        if gds:
            attempt = next(a for stage in view["stages"] for a in stage["attempts"]
                           if any(x.get("artifact_id") == gds["artifact_id"]
                                  for x in a.get("artifacts", [])))
            gds_path = Path(attempt["workspace"]) / gds["store_key"]
        stage_status = {stage["stage_key"]: stage["status"] for stage in view["stages"]}
        candidate_files = list(root.glob("runtime-workspaces/*/*/attempt-*/codex-passing-*.sv"))
        row_checks = {
            "natural_language_nonempty": bool(report["input"].get("natural_language")),
            "golden_rtl_not_supplied": report["input"].get("golden_rtl_supplied_to_agents") is False,
            "benchmark_tb_not_supplied": report["input"].get("benchmark_testbench_supplied_to_agents") is False,
            "fixed_server_model": report.get("model_policy") == "platform-managed codex-cli:gpt-5.6-terra",
            "specir_ready": report["spec_session"]["state"].get("ready_for_execution") is True,
            "independent_oracle": final_candidate["provenance"].get("oracle_origin")
                == "independent_verifier_agent",
            "oracle_bytes_match_sha": oracle_path.is_file() and _sha(oracle_path) == oracle_sha,
            "oracle_receipt_present": bool(receipt_paths)
                and receipt.get("origin") == "independent_verifier_agent",
            "rtl_bytes_match_sha": rtl_path.is_file() and _sha(rtl_path) == rtl_sha,
            "generated_rtl_differs_from_golden_fixture": golden.is_file() and _sha(golden) != rtl_sha,
            "at_least_eight_upstream_candidates": len(candidate_files) >= 8,
            "lint_passed": any(item["candidate_id"] == final_candidate["candidate_id"]
                               and item["check_kind"] == "compile_lint"
                               and item["status"] == "passed" for item in checks),
            "simulation_passed": any(item["candidate_id"] == final_candidate["candidate_id"]
                                     and item["check_kind"] == "simulation"
                                     and item["status"] == "passed" for item in checks),
            "mutation_passed": final_mutation["status"] == "passed"
                and mutation["eligible"] is True and mutation["mutation_score"] >= .8,
            "orfs_terminal_success": view["run"]["status"] == "succeeded",
            "gds_registered_nonempty_hash_matched": bool(gds and gds_path and gds_path.is_file()
                and gds_path.stat().st_size > 0 and _sha(gds_path) == gds["sha256"]),
        }
        rows.append({
            "design": design, "experiment": str(root), "checks": row_checks,
            "spec_id": report["specir"]["spec_id"], "testbench_sha256": oracle_sha,
            "rtl_sha256": rtl_sha, "candidate_files": len(candidate_files),
            "rtl_revisions": int(pipeline.get("rtl_revision") or 0),
            "revision_history": pipeline.get("revision_history") or [],
            "mutation": {key: mutation[key] for key in (
                "generated_count", "executable_count", "killed_count",
                "survived_count", "invalid_count", "mutation_score")},
            "orfs_run_id": orfs_run_id, "orfs_stage_status": stage_status,
            "gds": ({"artifact_id": gds["artifact_id"], "sha256": gds["sha256"],
                     "size_bytes": gds["size_bytes"]} if gds else None),
        })
    failed = []
    for raw in args.failed_experiment:
        root = raw.expanduser().resolve(); report_path = root / "report.json"
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
            failed.append({"experiment": str(root), "design": report.get("design"),
                           "status": report.get("status"), "failure": report.get("failure")})
    suite_checks = {
        "fixed_four_designs": {row["design"] for row in rows}
            == {"gcd", "fifo", "uart_tx", "ibex_alu"},
        "all_design_gates_passed": all(all(row["checks"].values()) for row in rows),
        "failure_preimages_retained": len(failed) >= 2 and all(
            item["status"] == "failed" for item in failed),
        "revision_evidence_present": sum(row["rtl_revisions"] > 0 for row in rows) >= 2,
    }
    result = {
        "schema_version": 1, "kind": "v2_real_rtl_suite_audit",
        "status": "passed" if all(suite_checks.values()) else "failed",
        "checks": suite_checks, "design_rows": sorted(rows, key=lambda item: item["design"]),
        "failed_preimages": failed,
        "claim_boundary": (
            "Four one-seed natural-language generations passed independent generated-testbench, "
            "lint, simulation, mutation and full ORFS/GDS gates. This establishes fixed-suite "
            "feasibility, not arbitrary-spec generalization or a multi-seed functional pass rate."),
    }
    destination = args.output.expanduser().resolve(); destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2),
                           encoding="utf-8")
    print(json.dumps({"output": str(destination), "status": result["status"],
                      "designs": len(rows), "failed_preimages": len(failed),
                      "revised_designs": sum(row["rtl_revisions"] > 0 for row in rows)},
                     ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
