#!/usr/bin/env python3
"""Run the bounded P14 evidence -> BO/GP -> Runtime -> learning acceptance."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "packages/contracts/src", ROOT / "packages/execution/src",
               ROOT / "packages/scheduler/src", ROOT / "packages/analysis/src"):
    sys.path.insert(0, str(source))

from openroad_platform_analysis import (  # noqa: E402
    BehaviorCloningShadowPolicy,
    EvidenceKnowledgeRecordV2,
    EvidenceRAG,
    LearningDatasetStore,
    MultiObjectiveBayesianOptimizer,
    OfflineLinearQShadowPolicy,
    OptimizationStudyStore,
    RuntimeEvidenceExporter,
    build_trajectory,
    split_by_design,
)
from openroad_platform_contracts import (  # noqa: E402
    EvidencePointer,
    ExperimentCandidate,
    ExperimentPlan,
    LearningContext,
    ObjectiveSpec,
    OptimizationStudy,
    ParameterSpec,
    RuntimeStatus,
)
from openroad_platform_execution import (  # noqa: E402
    PluginRegistry,
    ToolchainConfig,
    build_orfs_task,
    orfs_plugin_manifest,
)
from openroad_platform_scheduler import (  # noqa: E402
    CampaignStore,
    OptimizationCampaignBridge,
    RuntimeStore,
    StageAwareCampaignManager,
    WorkflowRuntime,
)


P5_RUNS = ("c1a2d0ef865f46879e148cae28d5d715",
           "c9eccfeb21e24843b461805713c2a647")
P2_RUN = "d154f31a0ac64e3cb068329dfcde3149"
OBJECTIVES = (
    ObjectiveSpec("wirelength_um", "min", 1.0),
    ObjectiveSpec("setup_wns_ns", "max", 1.0),
    ObjectiveSpec("power_W", "min", 1.0),
)
PARAMETERS = (
    ParameterSpec("core_utilization_pct", 10.0, 60.0),
    ParameterSpec("place_density", 0.35, 0.70),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--p5-runtime-db", type=Path,
                        default=ROOT / "runs/p5-acceptance-20260804-02/runtime.snapshot.db")
    parser.add_argument("--p2-runtime-db", type=Path,
                        default=ROOT / "runs/p2-acceptance-20260804-02/runtime.snapshot.db")
    parser.add_argument("--orfs-root", type=Path,
                        default=Path.home() / "OpenROAD-flow-scripts")
    parser.add_argument("--openroad-bin", type=Path, default=Path.home() / "bin/openroad")
    parser.add_argument("--yosys-bin", type=Path, default=Path.home() / "bin/yosys")
    parser.add_argument("--klayout-bin", type=Path, default=Path.home() / "bin/klayout")
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--max-parallel", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.max_parallel <= 2:
        raise ValueError("P14 acceptance max_parallel must be 1 or 2")
    output = args.output_root.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(output)
    output.mkdir(parents=True, exist_ok=True)

    toolchain = ToolchainConfig(
        name="orfs-2d-baseline", orfs_root=args.orfs_root.expanduser().resolve(),
        openroad_bin=args.openroad_bin.expanduser().resolve(),
        yosys_bin=args.yosys_bin.expanduser().resolve(),
        klayout_bin=args.klayout_bin.expanduser().resolve(),
    )
    toolchain.validate()
    before = _shared_snapshot(toolchain)
    toolchain_id = "orfs-51ad123-openroad-63ed2e0fe5-yosys-d3e297fcd"
    p5_store = RuntimeStore(args.p5_runtime_db)
    p2_store = RuntimeStore(args.p2_runtime_db)
    p5_task = p5_store.get_run(P5_RUNS[0]).task_spec
    p2_task = p2_store.get_run(P2_RUN).task_spec
    p5_context = _context(p5_task, toolchain_id)
    p2_context = _context(p2_task, toolchain_id)

    dataset = LearningDatasetStore(output / "learning_observations.db")
    studies = OptimizationStudyStore(output / "optimization_studies.db")
    primary_study = OptimizationStudy(
        study_id="p14-adder-bo-gp-v1", design_id=p5_task.design_id,
        context_fingerprint=p5_context.fingerprint, parameter_space=PARAMETERS,
        objectives=OBJECTIVES, max_runs=8, seed=14015, status="active",
    )
    heldout_study = OptimizationStudy(
        study_id="p14-mux-heldout-v1", design_id=p2_task.design_id,
        context_fingerprint=p2_context.fingerprint, parameter_space=PARAMETERS,
        objectives=OBJECTIVES, max_runs=6, seed=14016, status="active",
    )
    studies.create(primary_study)
    studies.create(heldout_study)
    for run_id in P5_RUNS:
        observation = RuntimeEvidenceExporter(p5_store).export_run(run_id, p5_context)
        dataset.add(observation)
        studies.add_observation(primary_study.study_id, observation)
    heldout_seed = RuntimeEvidenceExporter(p2_store).export_run(P2_RUN, p2_context)
    dataset.add(heldout_seed)
    studies.add_observation(heldout_study.study_id, heldout_seed)

    optimizer = MultiObjectiveBayesianOptimizer(pool_size=512, exploration=0.05)
    proposal = optimizer.propose(primary_study, studies.observations(primary_study.study_id))
    if not proposal.predictions:
        raise RuntimeError("Warm-start did not produce GP predictions")
    replayed = optimizer.propose(primary_study,
                                 studies.observations(primary_study.study_id))
    if replayed != proposal:
        raise RuntimeError("BO proposal is not deterministic under the fixed seed")
    studies.save_proposal(proposal)
    plan = _benchmark_plan(proposal, primary_study)

    runtime_db = Path("/tmp/openroad-platform-p14-runtime") / f"{output.name}.db"
    runtime_db.parent.mkdir(parents=True, exist_ok=True)
    if runtime_db.exists():
        raise FileExistsError(runtime_db)
    runtime = WorkflowRuntime(
        RuntimeStore(runtime_db),
        PluginRegistry([orfs_plugin_manifest(toolchain,
                                             default_timeout_seconds=args.timeout)]),
        workspace_root=output / "runtime-workspaces", worker_id="p14-real-acceptance",
        lease_seconds=60,
    )
    manager = StageAwareCampaignManager(CampaignStore(output / "campaigns.db"), runtime)
    bridge = OptimizationCampaignBridge(manager)
    primary_base = build_orfs_task(
        Path(p5_task.inputs["rtl"]["path"]), project_id="openroad-platform",
        design_id=p5_task.design_id, top=p5_task.inputs["top"],
        platform_name="nangate45", target_stage="finish", clock_period_ns=10.0,
        core_utilization_pct=35.0, place_density=0.45,
        timeout_seconds=args.timeout, stage_timeout_seconds=min(args.timeout, 3600),
        task_id="p14-primary-base", labels={"phase": "P14", "role": "benchmark"},
    )
    primary_campaign = bridge.create(
        "P14 BO/random/grid comparable real finish", primary_base, plan,
        max_parallel=args.max_parallel, objective_metric=None, top_k=3, max_repairs=1,
    )
    started = time.monotonic()
    primary_view = manager.run_until_terminal(
        primary_campaign, timeout_seconds=args.timeout * 3,
    )
    if primary_view["counts"].get("succeeded") != 3:
        raise RuntimeError(f"Primary P14 campaign did not produce 3 successes: {primary_view}")
    bridge.ingest_terminal(
        primary_campaign, context=p5_context, exporter=RuntimeEvidenceExporter(runtime.store),
        study_store=studies, study_id=primary_study.study_id,
    )

    heldout_base = build_orfs_task(
        Path(p2_task.inputs["rtl"]["path"]), project_id="openroad-platform",
        design_id=p2_task.design_id, top=p2_task.inputs["top"],
        platform_name="nangate45", target_stage="finish", clock_period_ns=10.0,
        core_utilization_pct=10.0, place_density=0.45,
        timeout_seconds=args.timeout, stage_timeout_seconds=min(args.timeout, 3600),
        task_id="p14-heldout-base", labels={"phase": "P14", "role": "heldout"},
    )
    heldout_campaign = manager.create_grid(
        "P14 held-out mux real finish", heldout_base,
        {"core_utilization_pct": (20.0, 32.0), "place_density": (0.40,)},
        max_parallel=args.max_parallel, max_repairs=1, max_total_runs=4,
        objective_metric=None, top_k=2,
    )
    heldout_view = manager.run_until_terminal(
        heldout_campaign, timeout_seconds=args.timeout * 2,
    )
    if heldout_view["counts"].get("succeeded") != 2:
        raise RuntimeError(f"Held-out P14 campaign did not produce 2 successes: {heldout_view}")
    heldout_new = []
    for member in manager.store.members(heldout_campaign):
        if member.run_id is None:
            continue
        observation = RuntimeEvidenceExporter(runtime.store).export_run(
            member.run_id, p2_context,
        )
        dataset.add(observation)
        studies.add_observation(heldout_study.study_id, observation)
        heldout_new.append(observation)
    for observation in studies.observations(primary_study.study_id)[2:]:
        dataset.add(observation)

    primary_observations = studies.observations(primary_study.study_id)
    heldout_observations = studies.observations(heldout_study.study_id)
    primary_steps = build_trajectory(
        primary_observations, OBJECTIVES, trajectory_id="trajectory-p14-adder-v1",
        runtime_scale_seconds=3600,
    )
    heldout_steps = build_trajectory(
        heldout_observations, OBJECTIVES, trajectory_id="trajectory-p14-mux-v1",
        runtime_scale_seconds=3600,
    )
    train_steps, test_steps = split_by_design(
        primary_steps + heldout_steps, {p2_task.design_id},
    )
    behavior = BehaviorCloningShadowPolicy().fit(train_steps)
    heldout_state = test_steps[-1].next_state
    behavior_shadow = behavior.propose(
        design_id=p2_task.design_id, context_fingerprint=p2_context.fingerprint,
        state=heldout_state, evidence=test_steps[-1].evidence,
        parameter_space=PARAMETERS,
    )
    linear_q = OfflineLinearQShadowPolicy().fit(train_steps)
    q_shadow = linear_q.propose(
        design_id=p2_task.design_id, context_fingerprint=p2_context.fingerprint,
        state=heldout_state,
        candidate_actions=(
            {"core_utilization_pct": 20.0, "place_density": 0.40},
            {"core_utilization_pct": 32.0, "place_density": 0.40},
        ), evidence=test_steps[-1].evidence,
    )

    rag = EvidenceRAG(output / "evidence_rag.db")
    best = min((item for item in primary_observations if item.status == "succeeded"),
               key=lambda item: item.metrics["wirelength_um"])
    best_pointer = next(item for item in best.evidence if item.ref.startswith("artifact:"))
    record = EvidenceKnowledgeRecordV2(
        record_id="knowledge-v2-p14-observed-best-wirelength",
        claim=("Observed Nangate45 finish result: bounded core utilization and placement "
               "density produced a DRC-clean routed design with measured timing, power, "
               "and wirelength QoR."),
        knowledge_type="observed_fact", context=p5_context, evidence=best_pointer,
        verified=True, tags=("OpenROAD", "QoR", "wirelength", "DRC"),
    )
    rag.add(record)
    bundle = rag.retrieve("observed wirelength DRC QoR", p5_context,
                          action_eligible_only=True)
    rag.replay(bundle, p5_context)
    wrong_context_bundle = rag.retrieve(
        "observed wirelength DRC QoR",
        dataclasses.replace(p5_context, toolchain_id="orfs-wrong-toolchain"),
        action_eligible_only=True,
    )

    primary_members = {member.run_id: member for member in manager.store.members(primary_campaign)}
    method_results = []
    bo_observation = None
    for observation in primary_observations[2:]:
        candidate_id = primary_members[observation.run_id].task_spec.labels[
            "optimizer_candidate_id"
        ]
        method = "bo_gp" if candidate_id == proposal.candidate_id else (
            "random" if candidate_id.startswith("candidate-random") else "grid_rule"
        )
        if method == "bo_gp":
            bo_observation = observation
        method_results.append({
            "method": method, "candidate_id": candidate_id,
            "run_id": observation.run_id, "attempt_id": observation.attempt_id,
            "parameters": observation.parameters, "observed_metrics": observation.metrics,
            "evidence": [item.to_dict() for item in observation.evidence],
        })
    if bo_observation is None:
        raise RuntimeError("BO candidate observation was not found")
    prediction_error = []
    for prediction in proposal.predictions:
        observed = bo_observation.metrics[prediction.metric_name]
        prediction_error.append({
            "metric": prediction.metric_name, "predicted_mean": prediction.mean,
            "predicted_stddev": prediction.stddev, "observed": observed,
            "absolute_error": abs(observed - prediction.mean),
        })

    after = _shared_snapshot(toolchain)
    _checkpoint_copy(runtime_db, output / "runtime.snapshot.db")
    elapsed = round(time.monotonic() - started, 3)
    summary = {
        "schema_version": 1, "phase": "P14", "accepted": True,
        "acceptance_class": "real-evidence-learning-closed-loop-v1",
        "elapsed_seconds": elapsed,
        "runtime_authoritative": True,
        "optimizer_execution_allowed": False,
        "shadow_policy_execution_allowed": False,
        "new_real_orfs_runs": len(runtime.store.list_runs(limit=100)),
        "successful_real_orfs_runs": sum(
            run.status is RuntimeStatus.SUCCEEDED
            for run in runtime.store.list_runs(limit=100)
        ),
        "preserved_failed_real_orfs_runs": sum(
            run.status is RuntimeStatus.FAILED
            for run in runtime.store.list_runs(limit=100)
        ),
        "approved_run_budget": 24,
        "max_parallel": args.max_parallel,
        "toolchain": {"id": toolchain_id, "orfs_commit": before["orfs_head"],
                      "shared_unchanged": before == after},
        "contexts": {"primary": p5_context.to_dict(), "held_out": p2_context.to_dict()},
        "studies": {
            "primary": studies.describe(primary_study.study_id),
            "held_out": studies.describe(heldout_study.study_id),
        },
        "bo_replay": {"deterministic": replayed == proposal,
                      "proposal": proposal.to_dict(),
                      "prediction_error": prediction_error},
        "method_comparison": method_results,
        "campaigns": {"primary": primary_view, "held_out": heldout_view},
        "offline_rl": {
            "train_designs": sorted({step.design_id for step in train_steps}),
            "held_out_designs": sorted({step.design_id for step in test_steps}),
            "train_steps": len(train_steps), "held_out_steps": len(test_steps),
            "behavior_cloning_shadow": behavior_shadow.to_dict(),
            "linear_q_shadow": q_shadow.to_dict(),
        },
        "rag": {"eligible_bundle": bundle.to_dict(),
                "wrong_toolchain_result_count": len(wrong_context_bundle.records)},
        "observation_counts": {"primary": len(primary_observations),
                               "held_out": len(heldout_observations),
                               "total": len(dataset.list())},
        "artifacts": {
            "runtime_db": _file_record(output / "runtime.snapshot.db"),
            "campaign_db": _file_record(output / "campaigns.db"),
            "study_db": _file_record(output / "optimization_studies.db"),
            "learning_db": _file_record(output / "learning_observations.db"),
            "rag_db": _file_record(output / "evidence_rag.db"),
        },
    }
    checks = {
        "five_real_gds_runs_succeeded": (
            primary_view["counts"].get("succeeded") == 3
            and heldout_view["counts"].get("succeeded") == 2
        ),
        "failed_preimages_preserved_and_repaired": (
            heldout_view["counts"].get("failed") == 2
            and sum(item["kind"] == "repair_created"
                    for item in heldout_view["decisions"]) == 2
        ),
        "all_observations_are_observed": all(
            item.source == "observed" for item in primary_observations + heldout_observations
        ),
        "bo_predictions_are_predicted": all(item.source == "predicted"
                                               for item in proposal.predictions),
        "bo_replay_deterministic": replayed == proposal,
        "design_split_has_no_leakage": not ({step.design_id for step in train_steps}
                                             & {step.design_id for step in test_steps}),
        "shadow_policies_non_executable": (
            behavior_shadow.execution_allowed is False
            and q_shadow.execution_allowed is False
        ),
        "wrong_toolchain_rag_rejected": not wrong_context_bundle.records,
        "shared_toolchain_unchanged": before == after,
        "budget_respected": (len(runtime.store.list_runs(limit=100)) <= 24
                             and args.max_parallel <= 2),
        "five_successful_runs_have_registered_gds": _successful_runs_have_gds(
            runtime.store, expected=5,
        ),
    }
    summary["checks"] = checks
    summary["accepted"] = all(checks.values())
    _write_json(output / "acceptance_summary.json", summary)
    print(json.dumps({"accepted": summary["accepted"], "elapsed_seconds": elapsed,
                      "new_real_orfs_runs": summary["new_real_orfs_runs"],
                      "checks": checks}, indent=2))
    return 0 if summary["accepted"] else 2


def _context(task, toolchain_id: str) -> LearningContext:
    rtl = task.inputs.get("rtl")
    if not isinstance(rtl, dict) or not isinstance(rtl.get("sha256"), str):
        raise ValueError("Historical task has no versioned RTL input")
    return LearningContext(
        design_id=task.design_id, design_fingerprint=rtl["sha256"],
        platform="nangate45", pdk_id="nangate45-public-v1",
        toolchain_id=toolchain_id, flow_stage="finish",
        metric_parser_version="orfs-analysis-report-v1",
    )


def _benchmark_plan(proposal, study) -> ExperimentPlan:
    rng = np.random.default_rng(study.seed + 991)
    random_parameters = {
        item.name: float(rng.uniform(item.lower, item.upper)) for item in study.parameter_space
    }
    candidates = (
        ExperimentCandidate(
            candidate_id=proposal.candidate_id, parameters=dict(proposal.parameters),
            source_trial_id=proposal.proposal_id,
            evidence_refs=tuple(item.ref for item in proposal.evidence),
        ),
        ExperimentCandidate(
            candidate_id="candidate-random-seed-14015", parameters=random_parameters,
            source_trial_id="random-baseline-seed-14015",
            evidence_refs=(f"source:study:{study.study_id}",),
        ),
        ExperimentCandidate(
            candidate_id="candidate-grid-rule-45-055",
            parameters={"core_utilization_pct": 45.0, "place_density": 0.55},
            source_trial_id="grid-rule-baseline-v1",
            evidence_refs=(f"source:study:{study.study_id}",),
        ),
    )
    return ExperimentPlan(
        plan_id="plan-p14-comparable-methods-v1", producer="p14-benchmark-v1",
        design_id=study.design_id, platform="openroad",
        baseline_parameters={"clock_period_ns": 10.0}, candidates=candidates,
        max_child_runs=3,
        provenance={"study_id": study.study_id, "optimizer_proposal": proposal.to_dict(),
                    "methods": ["bo_gp", "random", "grid_rule"],
                    "predictions_are_canonical_metrics": False},
    )


def _successful_runs_have_gds(store: RuntimeStore, *, expected: int) -> bool:
    runs = store.list_runs(limit=100)
    succeeded = [run for run in runs if run.status is RuntimeStatus.SUCCEEDED]
    if len(succeeded) != expected:
        return False
    for run in succeeded:
        attempts = [attempt for stage in store.list_stages(run.run_id)
                    for attempt in store.list_attempts(stage.stage_run_id)]
        successful = [item for item in attempts if item.status is RuntimeStatus.SUCCEEDED]
        if not successful or not any(item["kind"] == "gds"
                                     for item in store.artifacts(successful[-1].attempt_id)):
            return False
    return True


def _shared_snapshot(toolchain: ToolchainConfig) -> dict:
    return {
        "orfs_head": _command(["git", "-C", str(toolchain.orfs_root),
                               "rev-parse", "HEAD"]),
        "orfs_status_sha256": _text_sha(_command([
            "git", "-C", str(toolchain.orfs_root), "status", "--porcelain=v2",
            "--untracked-files=all",
        ])),
        "orfs_diff_sha256": _text_sha(_command([
            "git", "-C", str(toolchain.orfs_root), "diff", "--binary", "HEAD",
        ])),
        "files": {"openroad": _file_record(toolchain.openroad_bin),
                  "yosys": _file_record(toolchain.yosys_bin),
                  "klayout": _file_record(toolchain.klayout_bin),
                  "platform": _file_record(toolchain.flow_home
                                           / "platforms/nangate45/config.mk")},
    }


def _command(argv: list[str]) -> str:
    return subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, timeout=60, check=False).stdout.rstrip()


def _text_sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _file_record(path: Path) -> dict:
    resolved = path.resolve()
    return {"path": str(resolved), "size_bytes": resolved.stat().st_size,
            "sha256": _sha256(resolved)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checkpoint_copy(source: Path, destination: Path) -> None:
    with sqlite3.connect(source, timeout=30) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copy2(source, destination)


def _write_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
