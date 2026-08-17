#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify L2 agent mode: BO/GP recommendation with rationale -> human review -> execute.

Uses the live observations (mux synthesis) to build an optimization study,
propose a next parameter candidate via MultiObjective Bayesian Optimization,
turn it into a PolicyRecommendation (rationale + confidence), record a human
decision (approved), then execute the candidate and auto-collect the result.
"""
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for source in (ROOT / "packages/contracts/src", ROOT / "packages/execution/src",
               ROOT / "packages/scheduler/src", ROOT / "packages/analysis/src",
               ROOT / "packages/visualization/src"):
    sys.path.insert(0, str(source))

from openroad_platform_contracts import (  # noqa: E402
    LearningObservation, ObjectiveSpec, OptimizationStudy, ParameterSpec,
)
from openroad_platform_analysis import (  # noqa: E402
    MultiObjectiveBayesianOptimizer, build_recommendation,
)
from openroad_platform_execution import build_orfs_task  # noqa: E402
from apps.api.app import ApiState  # noqa: E402

OWNER = "user-ea0c3d1f4520448d99a22d2dc7f7b250"  # yuanwenjie (live)
PROJECT = "openroad-platform"
TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}


def main() -> int:
    state = ApiState(
        ROOT / "var" / "platform.db", ROOT / "var" / "uploads",
        ROOT.parent / "OpenROAD-flow-scripts",
        design_root=ROOT / "var" / "designs", legacy_root=ROOT.parent / "iccad",
        runtime_db_path=ROOT / "var" / "public" / "runtime.db",
        campaign_db_path=ROOT / "var" / "public" / "campaign.db",
        optimization_db_path=ROOT / "var" / "public" / "optimization.db",
        auth_db_path=ROOT / "var" / "public" / "web-auth.db",
        byok_transport_secure=False, load_taiwei_plugin=False,
    )
    obs = state.tenant_learning_store.list(OWNER, PROJECT)
    print("total observations:", len(obs))
    # pick observations of the mux design (same learning context)
    by_design = {}
    for o in obs:
        by_design.setdefault(o.context.design_id, []).append(o)
    print("designs:", {k: len(v) for k, v in by_design.items()})
    mux_obs = max(by_design.values(), key=len)
    if len(mux_obs) < 2:
        print("need >=2 observations of one design; abort")
        return 1
    context = mux_obs[0].context

    study = OptimizationStudy(
        study_id=f"study-{uuid.uuid4().hex[:16]}",
        design_id=context.design_id, context_fingerprint=context.fingerprint,
        parameter_space=(ParameterSpec(name="core_utilization_pct", lower=1, upper=99),),
        objectives=(ObjectiveSpec(metric_name="area", direction="min"),),
        max_runs=16, seed=1,
    )
    study_id = state.optimization_store.create(study)
    for o in mux_obs:
        state.optimization_store.add_observation(study_id, o)
    study_obs = state.optimization_store.observations(study_id)
    print("study:", study_id, "observations:", len(study_obs))

    proposal = MultiObjectiveBayesianOptimizer(pool_size=512, exploration=0.05)\
        .propose(study, study_obs)
    state.optimization_store.save_proposal(proposal)
    print("proposal parameters:", json.dumps(proposal.parameters))
    print("acquisition_value:", round(proposal.acquisition_value, 4))

    calibration = None
    try:
        calibration = state.calibrate_study(study_id)
    except Exception as exc:
        print("calibration skipped:", type(exc).__name__)
    recommendation = build_recommendation(
        study, proposal, study_obs,
        held_out_error=(calibration["calibration"]["normalized_rmse"]
                        if calibration else None),
        interval_coverage=(calibration["calibration"]["interval_coverage"]
                           if calibration else None),
        worst_case_cost_seconds=1200.0,
    )
    rec_id = state.recommendation_store.save(OWNER, recommendation)
    rec = recommendation.to_dict()
    print("recommendation:", json.dumps({
        "id": rec_id, "policy_kind": rec["policy_kind"],
        "parameters": rec["parameters"],
        "rationale": rec["rationale"],
        "confidence": rec["confidence"]["overall"],
        "confidence_reasons": rec["confidence"]["reasons"],
        "permission_tier": rec["permission_tier"],
    }, ensure_ascii=False, indent=2))

    # human review -> accept
    decision = state.recommendation_store.decide(
        OWNER, rec_id, action="accepted")
    print("decision:", json.dumps(decision.to_dict(), ensure_ascii=False)[:300])

    # execute the proposed candidate through the real runtime
    rtl_path = ROOT / "tests" / "fixtures" / "p2_mux_2to1.v"
    candidate = build_orfs_task(
        rtl_path, project_id=PROJECT, design_id="mux-2to1", top="mux_2to1",
        target_stage="synth", stage_timeout_seconds=300, timeout_seconds=900,
        core_utilization_pct=int(proposal.parameters["core_utilization_pct"]),
        labels={"source": "agent-l2", "policy_kind": "bo-gp",
                "recommendation_id": rec_id, "owner_id": OWNER},
    )
    run = state.runtime.submit(candidate)
    while run.status.value not in TERMINAL:
        run = state.runtime.execute_once(run.run_id)
    result = state.auto_collect_terminal_run(run.run_id)
    print("candidate run:", run.status.value, "auto:", json.dumps(result,
          ensure_ascii=False))

    after = len(state.tenant_learning_store.list(OWNER, PROJECT))
    print("observations now:", after)
    assert run.status.value == "succeeded", run.status.value
    assert result.get("action") in {"collect", "skipped"}, result
    print("L2_VERIFY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
