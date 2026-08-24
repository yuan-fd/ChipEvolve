from openroad_platform_analysis import (diagnosis_packet, paired_replica_seeds,
                                        relative_utility, stalled_decision,
                                        summarize_replicates)
from openroad_platform_contracts import (EvidencePointer, LearningContext,
                                         LearningObservation, ObjectiveSpec)


def observation(index, area, slack, *, status="succeeded"):
    context = LearningContext(
        design_id="gcd", design_fingerprint="a" * 64, platform="nangate45",
        pdk_id="nangate45", toolchain_id="orfs-fixed", flow_stage="finish",
        metric_parser_version="runtime-v2",
    )
    return LearningObservation(
        observation_id=f"obs-{index}", context=context,
        parameters={"place_density": .5}, metrics={"area_um2": area, "setup_wns_ns": slack},
        metric_units={"area_um2": "um2", "setup_wns_ns": "ns"}, status=status,
        cost_seconds=1, run_id=f"run-{index}", attempt_id=f"attempt-{index}",
        evidence=(EvidencePointer(ref=f"run:run-{index}", sha256=str(index) * 64),),
    )


def test_replicate_summary_utility_and_stall_are_not_best_single_run():
    objectives = (ObjectiveSpec("area_um2", "min", .6),
                  ObjectiveSpec("setup_wns_ns", "max", .4))
    baseline = summarize_replicates(
        [observation(1, 100, .10), observation(2, 102, .12), observation(3, 98, .11)],
        objectives, ({"metric": "setup_wns_ns", "operator": ">=", "threshold": 0},))
    candidate = summarize_replicates(
        [observation(4, 90, .08), observation(5, 110, .09), observation(6, 92, .10)],
        objectives, ({"metric": "setup_wns_ns", "operator": ">=", "threshold": 0},))
    assert candidate["metrics"]["area_um2"]["median"] == 92
    assert candidate["metrics"]["area_um2"]["maximum"] == 110
    utility = relative_utility(candidate, baseline, objectives)
    assert utility is not None
    rejected = stalled_decision(candidate_utility=utility, best_utility=utility,
                                minimum_relative_improvement=.01, stalled_rounds=2)
    assert rejected["promoted"] is False
    assert rejected["stalled_rounds"] == 3


def test_failed_constraint_blocks_promotion_and_produces_non_executable_diagnosis():
    objectives = (ObjectiveSpec("area_um2", "min"),)
    summary = summarize_replicates(
        [observation(1, 90, -.1), observation(2, 91, .1)], objectives,
        ({"metric": "setup_wns_ns", "operator": ">=", "threshold": 0},))
    assert summary["eligible"] is False
    assert relative_utility(summary, summary, objectives) is None
    packet = diagnosis_packet([{"round": 1, "summary": summary}], objectives)
    assert packet["execution_allowed"] is False
    assert packet["violated_constraints"]


def test_paired_replica_seeds_are_stable_distinct_and_seed_sensitive():
    first = paired_replica_seeds(20260825, 5)
    assert first == paired_replica_seeds(20260825, 5)
    assert len(first) == len(set(first)) == 5
    assert first != paired_replica_seeds(20260826, 5)
