from __future__ import annotations

import dataclasses

import pytest

from openroad_platform_analysis import (
    RecommendationStore, automation_envelope, build_recommendation,
)
from openroad_platform_contracts import (
    EvidencePointer, LearningContext, LearningObservation, ObjectiveSpec,
    OptimizationStudy, OptimizerProposal, ParameterSpec,
)
from openroad_platform_execution import (
    build_craft_flow_plan, craft_capability_matrix, craft_plan_to_task,
)


def context():
    return LearningContext("gcd", "a" * 64, "nangate45", "nangate45-public",
                           "orfs-fixed", "finish", "parser-1")


def study():
    return OptimizationStudy("p16-study", "gcd", context().fingerprint,
        (ParameterSpec("place_density", 0.3, 0.8),),
        (ObjectiveSpec("area_um2", "min"),), 64, 1)


def observations(count=10):
    return [LearningObservation(f"observation-{i}", context(),
        {"place_density": 0.49 + (i % 3) * .005}, {"area_um2": 100 - i},
        {"area_um2": "um2"}, "succeeded", 10, f"run-{i}", f"attempt-{i}",
        (EvidencePointer(f"artifact:a-{i}", f"{(i % 9)+1:x}" * 64),)) for i in range(count)]


def proposal():
    return OptimizerProposal("optimizer-p16", "p16-study", "candidate-p16", 1,
        {"place_density": .5}, (), .1,
        (EvidencePointer("source:study:p16-study", "b" * 64),))


def test_t1_decisions_are_audited_and_real_small_data_is_not_t2(tmp_path):
    recommendation = build_recommendation(study(), proposal(), observations())
    assert recommendation.confidence.sample_count == 10
    envelope = automation_envelope(recommendation, exact_context=True,
                                   study_opt_in=True, budget_available=True)
    assert envelope.status == "not_eligible"
    assert envelope.checks["minimum_samples"] is False
    store = RecommendationStore(tmp_path / "recommend.db")
    store.save("alice", recommendation)
    accepted = store.decide("alice", recommendation.recommendation_id, action="accepted",
                            parameter_bounds={"place_density": (.3, .8)})
    assert accepted.action == "accepted" and not accepted.execution_requested
    modified = store.decide("alice", recommendation.recommendation_id, action="modified",
                            parameters={"place_density": .6},
                            parameter_bounds={"place_density": (.3, .8)})
    assert modified.selected_parameters == {"place_density": .6}
    rejected = store.decide("alice", recommendation.recommendation_id, action="rejected")
    assert rejected.selected_parameters == {}
    with pytest.raises(ValueError, match="outside"):
        store.decide("alice", recommendation.recommendation_id, action="modified",
                     parameters={"place_density": 1.2},
                     parameter_bounds={"place_density": (.3, .8)})


def test_t2_gate_requires_all_evidence_and_is_one_candidate():
    recommendation = build_recommendation(study(), proposal(), observations(20),
                                          held_out_error=.1, interval_coverage=.9)
    envelope = automation_envelope(recommendation, exact_context=True,
                                   study_opt_in=True, budget_available=True)
    assert envelope.status == "eligible"
    assert envelope.maximum_candidates == 1
    assert envelope.execution_allowed is False
    denied = automation_envelope(recommendation, exact_context=False,
                                 study_opt_in=True, budget_available=True)
    assert denied.status == "not_eligible"


def test_craft_plan_maps_to_orfs_and_fails_closed_for_commercial_features(tmp_path):
    rtl = tmp_path / "gcd.v"; rtl.write_text("module gcd(input clk); endmodule\n")
    plan = build_craft_flow_plan(rtl, project_id="p16", design_id="gcd", top="gcd",
                                 target_stage="finish")
    matrix = craft_capability_matrix(plan)
    assert matrix["backends"]["openroad-orfs"]["mode"] == "executable-via-runtime"
    assert matrix["backends"]["implcraft-scriptgen"]["commercial_eda_executed"] is False
    task = craft_plan_to_task(plan, "openroad-orfs")
    assert task.plugin_id == "orfs"
    assert task.parameters["target_stage"] == "finish"
    assert task.labels["craft_backend"] == "openroad-orfs"
    commercial = dataclasses.replace(plan, required_capabilities=("prime-time-signoff",))
    with pytest.raises(ValueError, match="cannot satisfy"):
        craft_plan_to_task(commercial, "openroad-orfs")
    with pytest.raises(ValueError, match="cannot satisfy"):
        craft_plan_to_task(plan, "implcraft-scriptgen")


def test_same_floorplan_intent_maps_to_both_craft_backends(tmp_path):
    rtl = tmp_path / "tiny.v"; rtl.write_text("module tiny(input clk); endmodule\n")
    plan = build_craft_flow_plan(rtl, project_id="p16", design_id="tiny", top="tiny",
                                 target_stage="floorplan")
    open_task = craft_plan_to_task(plan, "openroad-orfs")
    impl_task = craft_plan_to_task(plan, "implcraft-scriptgen")
    assert open_task.design_id == impl_task.design_id == "tiny"
    assert open_task.inputs["clock"] == impl_task.inputs["clock"] == "clk"
    assert impl_task.labels["commercial_eda_executed"] == "false"
