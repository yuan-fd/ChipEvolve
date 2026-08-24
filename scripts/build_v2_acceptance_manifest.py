#!/usr/bin/env python3
"""Build the v2 claim-to-evidence acceptance ledger.

This script does not rerun experiments or improve their conclusions.  It
verifies the frozen aggregate records, hashes them, and copies each aggregate's
own claim boundary into one reviewable manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "artifacts/v2-acceptance-20260825/manifest.json"

EVIDENCE = {
    "rtl_generation": "artifacts/v2-real-rtl-suite-20260825/aggregate.json",
    "closed_loop": "artifacts/v2-multidesign-closed-loop-20260825/aggregate.json",
    "parameter_ablation": "artifacts/v2-parameter-ablation-multiseed-20260825/aggregate.json",
    "causal_learning": "artifacts/v2-learning-ablation-20260825/aggregate.json",
    "edair": "artifacts/v2-edair-ablation-20260825/aggregate.json",
    "agent_architecture": "artifacts/v2-agent-architecture-20260825/aggregate.json",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(relative: str) -> tuple[Path, dict[str, Any]]:
    path = ROOT / relative
    if not path.is_file():
        raise FileNotFoundError(relative)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{relative} must contain one JSON object")
    if value.get("status") != "passed":
        raise ValueError(f"{relative} is not an accepted aggregate")
    if not str(value.get("claim_boundary") or "").strip():
        raise ValueError(f"{relative} has no claim boundary")
    checks = value.get("checks")
    if not isinstance(checks, dict) or not checks or not all(checks.values()):
        raise ValueError(f"{relative} contains an unmet acceptance check")
    return path, value


def git_value(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, check=False,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def summarize(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    if kind == "rtl_generation":
        rows = data["design_rows"]
        return {
            "designs": [row["design"] for row in rows],
            "generation_seeds_per_design": 1,
            "registered_gds": len(rows),
            "mutation": {row["design"]: row["mutation"] for row in rows},
        }
    if kind == "closed_loop":
        rows = data["design_rows"]
        return {
            "designs": len(rows), "full_flow_runs": data["run_count"],
            "practical_threshold_relative_utility": 0.005,
            "threshold_designs": data["designs_meeting_practical_threshold"],
            "threshold_design_rate": data["practical_threshold_rate"],
        }
    if kind == "parameter_ablation":
        return {
            "bo_runs": data["bo_run_count"], "random_runs": data["random_run_count"],
            "bo_threshold_events": data["bo_threshold_events"],
            "random_threshold_events": data["random_threshold_events"],
            "design_median_wins": data["median_design_wins"],
        }
    if kind == "causal_learning":
        return {
            "source_interaction_um2": data["source_interaction"],
            "holdout_interaction_um2": data["holdout_interaction"],
            "holdout_result": "refuted/action_eligible=false",
        }
    if kind == "edair":
        return dict(data["totals"])
    if kind == "agent_architecture":
        return {
            "designs": len(data["design_rows"]),
            "phases": ["map", "semantic", "experiment", "hypothesis",
                       "implement", "validate", "review", "memory"],
            "terminal_statuses": sorted({row["status"] for row in data["design_rows"]}),
        }
    raise KeyError(kind)


def build() -> dict[str, Any]:
    records = []
    for kind, relative in EVIDENCE.items():
        path, data = read_json(relative)
        records.append({
            "capability": kind,
            "evidence_path": relative,
            "evidence_sha256": sha256(path),
            "status": data["status"],
            "checks": data["checks"],
            "summary": summarize(kind, data),
            "claim_boundary": data["claim_boundary"],
        })

    knowledge_path = ROOT / "knowledge/public-corpus.lock.json"
    knowledge = json.loads(knowledge_path.read_text(encoding="utf-8"))
    product_sources = [
        "apps/api/app.py", "apps/web/assets/app.js",
        "packages/analysis/src/openroad_platform_analysis/closed_loop.py",
        "packages/analysis/src/openroad_platform_analysis/optimization.py",
        "packages/execution/src/openroad_platform_execution/rtlscout_plugin.py",
    ]
    return {
        "schema_version": 1,
        "kind": "openroad_platform_v2_acceptance_ledger",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_git_commit": git_value("rev-parse", "HEAD"),
        "source_tree_dirty_at_generation": bool(git_value("status", "--porcelain")),
        "product_contract": {
            "entry": "POST /api/v2/closed-loops -> POST /api/v2/closed-loops/<id>/run-to-boundary",
            "baseline_role": "server-owned round 0 measurement, not a user mode",
            "optimizer": "server-owned coupled-parameter BO/GP",
            "removed_product_modes": ["standalone baseline", "sequential scan",
                                      "grid/manual tuning", "recommendation approval",
                                      "browser BYOK/API-key Provider"],
            "server_model": "gpt-5.6-terra",
            "source_hashes": {item: sha256(ROOT / item) for item in product_sources},
        },
        "evidence_records": records,
        "knowledge_lock": {
            "path": "knowledge/public-corpus.lock.json",
            "sha256": sha256(knowledge_path),
            "source_count": len(knowledge["sources"]),
            "claim_count": len(knowledge["claims"]),
            "embedding_version": knowledge["snapshot"]["embedding_version"],
            "boundary": "Bibliographic metadata and bounded claims are indexed; paper full text is not implied.",
        },
        "global_claim_boundaries": [
            "The four-design RTL suite is one-seed fixed-suite feasibility, not arbitrary-spec generalization.",
            "The BO/random ablation is descriptive and does not establish universal or statistically significant superiority.",
            "The causal holdout blocked one observed false transfer; it is not a population transfer-rate estimate.",
            "EDAIR fidelity and eight-phase orchestration do not by themselves prove QoR improvement.",
            "TimingECO, Resynth, EvoDRC and source-repair executors belong to v3 and are not v2 acceptance claims.",
            "Run counts from different aggregates may overlap and must not be summed without run-id deduplication.",
        ],
        "status": "passed",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = build()
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
