#!/usr/bin/env python3
"""Run a real repeated 2x2 intervention and a cross-design holdout.

This is a v2 research harness, not a product endpoint.  It uses the same
WorkflowRuntime/ORFS plugin and records every terminal run.  The source study
is completed before the held-out design is launched; a hypothesis and its
holdout controls are therefore fixed before seeing holdout outcomes.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from itertools import product
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.api.app import ApiState  # noqa: E402
from openroad_platform_analysis import (  # noqa: E402
    followup_from_interaction, paired_replica_seeds,
)
from openroad_platform_execution import build_orfs_task  # noqa: E402


METRIC = "finish__design__instance__area"
FIRST = "core_utilization_pct"
SECOND = "place_density"
LEVELS = {FIRST: (20.0, 65.0), SECOND: (.35, .75)}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_design(state: ApiState, *, design_name: str, repetitions: int,
                max_parallel: int, replica_or_seeds: tuple[int, ...]) -> tuple[dict, list[str]]:
    package = ROOT / "benchmarks" / "v2" / design_name
    manifest = json.loads((package / "package.json").read_text(encoding="utf-8"))
    rtl_path = package / manifest["golden_rtl"]
    design = state.designs.import_rtl(
        filename=f"{design_name}.sv", source=rtl_path.read_text(encoding="utf-8"),
        description=f"v2 causal holdout fixture: {design_name}", owner_id=None)
    run_ids: list[str] = []
    for first, second in product(LEVELS[FIRST], LEVELS[SECOND]):
        for replica in range(repetitions):
            task = build_orfs_task(
                state.designs.rtl_path(design["id"]), project_id="openroad-platform",
                design_id=design["id"], top=manifest["top"], clock="clk",
                platform_name="nangate45", target_stage="finish",
                clock_period_ns=10.0, core_utilization_pct=first,
                place_density=second, or_seed=replica_or_seeds[replica],
                labels={"v2_research": "causal-2x2-holdout",
                        "replica_index": str(replica),
                        "or_seed": str(replica_or_seeds[replica])},
            )
            run_ids.append(state.runtime.submit(task).run_id)
    with ThreadPoolExecutor(max_workers=min(max_parallel, len(run_ids))) as pool:
        for result in pool.map(state.runtime.execute_once, run_ids):
            del result
    return design, run_ids


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-design", choices=("gcd", "fifo", "uart_tx", "ibex_alu"),
                        default="gcd")
    parser.add_argument("--holdout-design", choices=("gcd", "fifo", "uart_tx", "ibex_alu"),
                        default="fifo")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--max-parallel", type=int, default=12)
    parser.add_argument("--seed", type=int, default=20260825,
                        help="frozen experiment seed used to derive paired OpenROAD seeds")
    parser.add_argument("--orfs-root", type=Path,
                        default=Path("/share/home/yuanwenjie/OpenROAD-flow-scripts"))
    args = parser.parse_args()
    if args.source_design == args.holdout_design:
        raise SystemExit("holdout design must differ from source design")
    if not 2 <= args.repetitions <= 8 or not 1 <= args.max_parallel <= 32:
        raise SystemExit("repetitions must be 2-8 and max-parallel 1-32")
    replica_or_seeds = paired_replica_seeds(args.seed, args.repetitions)
    output = args.output.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit("output must be a new or empty directory")
    output.mkdir(parents=True, exist_ok=True)
    state = ApiState(
        output / "platform.db", output / "uploads", args.orfs_root,
        design_root=output / "designs", legacy_root=output / "legacy",
        runtime_db_path=output / "runtime.db",
        optimization_db_path=output / "optimization.db", load_taiwei_plugin=False)
    if not state.health()["execution_ready"]:
        raise SystemExit("real ORFS execution is unavailable")

    source_design, source_ids = _run_design(
        state, design_name=args.source_design, repetitions=args.repetitions,
        max_parallel=args.max_parallel, replica_or_seeds=replica_or_seeds)
    source = state.causal_qor_report({
        "run_ids": source_ids, "first_parameter": FIRST,
        "second_parameter": SECOND, "metric": METRIC})
    followup = followup_from_interaction(
        source, first=FIRST, second=SECOND, metric=METRIC)
    if not followup.get("learning_eligible"):
        raise RuntimeError(f"source intervention is not eligible: {source}")
    expected = ("nonzero" if followup["hypothesis_kind"] == "interaction" else "zero")
    hypothesis = state.create_evolution_hypothesis({
        "run_ids": source_ids,
        "claim": followup["hypothesis"],
        "mechanism": (
            "Utilization changes placement whitespace while placement density changes "
            "the placer target; their joint physical constraint can be non-additive."),
        "context": {"source_design": args.source_design,
                    "source_design_id": source_design["id"],
                    "metric": METRIC, "expected_interaction": expected},
        "producer": "v2-controlled-intervention-agent",
        "proposed_intervention": {
            "kind": "pre_registered_cross_design_holdout",
            "held_out_design": args.holdout_design,
            "levels": {key: list(value) for key, value in LEVELS.items()},
            "repetitions": args.repetitions, "metric": METRIC,
            "hard_constraints": {"setup_wns_ns": ">=0", "drc_errors": "=0"},
            "execution_allowed": False,
        },
    })["hypothesis"]

    holdout_design, holdout_ids = _run_design(
        state, design_name=args.holdout_design, repetitions=args.repetitions,
        max_parallel=args.max_parallel, replica_or_seeds=replica_or_seeds)
    validation = state.validate_causal_holdout({
        "source_run_ids": source_ids, "holdout_run_ids": holdout_ids,
        "experiment_seed": args.seed,
        "paired_replica_or_seeds": list(replica_or_seeds),
        "first_parameter": FIRST, "second_parameter": SECOND, "metric": METRIC,
        "hypothesis_id": hypothesis["hypothesis_id"],
        "expected_direction": expected,
    })
    terminal = [state.runtime_store.get_run(run_id).status.value
                for run_id in source_ids + holdout_ids]
    report = {
        "schema_version": 1, "kind": "v2_real_causal_holdout",
        "source_design": source_design, "holdout_design": holdout_design,
        "source_run_ids": source_ids, "holdout_run_ids": holdout_ids,
        "experiment_seed": args.seed,
        "paired_replica_or_seeds": list(replica_or_seeds),
        "terminal_statuses": terminal, "source_report": source,
        "pre_registered_followup": followup, "hypothesis": hypothesis,
        "validation": validation,
        "runtime_db": {"path_name": "runtime.db", "sha256": _sha(output / "runtime.db")},
        "claim_boundary": (
            "A same-direction result supports only these two RTL fingerprints under the "
            "pinned Nangate45/ORFS context; a third design is still required for broader scope."),
    }
    destination = output / "report.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2),
                           encoding="utf-8")
    accepted = (
        all(status == "succeeded" for status in terminal)
        and source.get("causal_eligible") is True
        and validation.get("validation", {}).get("eligible") is True
        and validation.get("knowledge_card") is not None
    )
    print(json.dumps({
        "output": str(destination), "accepted": accepted,
        "source_interaction": source.get("interaction_effect"),
        "holdout_interaction": validation.get("holdout", {}).get("interaction_effect"),
        "holdout_outcome": validation.get("validation", {}).get("outcome"),
        "knowledge_status": (validation.get("knowledge_card") or {}).get("status"),
        "runs": len(terminal),
    }, ensure_ascii=False))
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
