from __future__ import annotations

from types import SimpleNamespace

from scripts.build_v2_paper_final_report import build


def _args(tmp_path):
    values = {
        "parameter": {
            "status": "passed", "run_count": 960, "cell_count": 1,
            "primary": {"mean_paired_difference": .02, "median_paired_difference": .02,
                        "bootstrap_median_95_ci": {"lower": .01, "upper": .03},
                        "sign_flip": {"p_value": .05}},
            "per_design_secondary": {"gcd": {"p_value": .1, "holm_adjusted_p_value": .4}},
            "cells": [{"design": "gcd", "seed": 41001,
                       "winner": "bo_gp", "paired_difference": .02,
                       "bo_gp": {"best_utility": .03, "failure_runs": 0},
                       "seeded_random": {"best_utility": .01, "failure_runs": 0}}],
            "threshold_hit_rate": {"bo_gp": 1.0, "seeded_random": .5},
            "objective_profile_replay": {"selection_difference_from_balanced": {
                "bo_gp": {"area": 1, "timing": 0, "performance": 0, "power": 1},
                "seeded_random": {"area": 0, "timing": 1, "performance": 1, "power": 0}}},
            "claim_boundary": "parameter boundary",
        },
        "learning": {
            "status": "passed", "ordered_pair_count": 1,
            "validated_pair_count": 0, "rejected_pair_count": 1,
            "arms": {"retrieval_only_counterfactual": {"false_transfer_rules_admitted": 1}},
            "pairs": [{"source": "gcd", "holdout": "fifo", "source_interaction": 1.0,
                       "holdout_interaction": -1.0, "outcome": "rejected",
                       "knowledge_status": "refuted", "accepted": True}],
            "claim_boundary": "learning boundary",
        },
        "rtl": {
            "status": "complete", "attempts": 5, "successes": 4,
            "first_authored_candidate_pass_rate": .2, "iterative_rescue_count": 3,
            "design_rows": [{"design": "gcd", "attempts": 5, "successes": 4,
                             "pass_rate": .8, "pass_at_k": {"5": 1.0},
                             "first_candidate_passes": 1, "iterative_rescues": 3,
                             "unique_rtl_hashes": 4, "unique_testbench_hashes": 5,
                             "ppa_vs_hidden_golden": {
                                 "area_um2": {"generated_median": 100.0, "golden_median": 90.0,
                                              "relative_generated_minus_golden": 1 / 9},
                                 "setup_wns_ns": {"generated_median": .1, "golden_median": .2},
                                 "power_W": {"generated_median": .01, "golden_median": .009}}}],
            "claim_boundary": "rtl boundary",
        },
        "edair": {
            "status": "complete",
            "totals": {
                "kpi_only": {"calls": 1, "answers": 12, "correct": 4, "accuracy": 1 / 3,
                             "unknown_rate": .5, "false_answer_rate": 1 / 6,
                             "mean_context_bytes": 100},
                "typed_edair": {"calls": 1, "answers": 12, "correct": 10, "accuracy": 5 / 6,
                                "unknown_rate": 1 / 12, "false_answer_rate": 1 / 12,
                                "mean_context_bytes": 1000}},
            "calls": [
                {"design": "gcd", "arm": "kpi_only", "total": 12, "correct": 4,
                 "unknown": 6, "false_answers": 2},
                {"design": "gcd", "arm": "typed_edair", "total": 12, "correct": 10,
                 "unknown": 1, "false_answers": 1}],
            "paired_statistics": {
                "mean_accuracy_difference": .5,
                "bootstrap_mean_95_ci": {"lower": .4, "upper": .6},
                "sign_flip": {"p_value": .01},
                "per_design_secondary": {"gcd": {
                    "mean_paired_difference": .5, "raw_p_value": .1,
                    "holm_adjusted_p_value": .4, "reject_at_0_05": False}}},
            "question_rows": [{"question_id": "q01", "label": "finish WNS",
                               "kpi_only": {"accuracy": .5},
                               "typed_edair": {"accuracy": 1.0},
                               "accuracy_difference": .5}],
            "claim_boundary": "edair boundary",
        },
        "agent": {
            "status": "passed",
            "arms": {
                "full_eight_phase_architecture": {},
                "no_checkpoint_counterfactual": {
                    "duplicate_runs_after_the_two_registered_partial_batch_interruptions": 2},
                "no_authority_gate_counterfactual": {"unsupported_executable_hypotheses": 3},
                "no_review_threshold_counterfactual": {"below_threshold_promotions": 2}},
            "real_trace_rows": [{"design": "gcd", "status": "completed",
                                 "hypothesis_events": 3, "implementation_events": 3,
                                 "validation_events": 4, "validation_events_with_run_ids": 4,
                                 "below_threshold_positive_candidates": 2}],
            "claim_boundary": "agent boundary",
        },
        "references": {"status": "passed", "run_count": 12,
                       "claim_boundary": "reference boundary"},
    }
    closed_loop = {"runtime_runs": [{"status": "succeeded"}] * 4,
                   "checkpoint": {"state": {"status": "completed", "round": 1,
                                              "best_utility": .02,
                                              "history": [{"round": 0, "parameters": {},
                                                           "utility": 0.0,
                                                           "summary": {"eligible": True,
                                                                       "constraints": [],
                                                                       "metrics": {}}}]}},
                   "claim_boundary": "external boundary"}
    values["aes"] = closed_loop
    values["jpeg"] = closed_loop
    paths = {}
    for name, value in values.items():
        path = tmp_path / f"{name}.json"
        path.write_text(__import__("json").dumps(value), encoding="utf-8")
        paths[name] = path
    return SimpleNamespace(**paths)


def test_paper_report_renders_all_evidence_sections(tmp_path):
    document, ledger, summary = build(_args(tmp_path))

    assert "生成 RTL 与隐藏参考 RTL" in document
    assert "全部 40 个 design×seed" in document
    assert "十二组跨设计因果复验" in document
    assert "分设计拆开看" in document
    assert "低于阈值正波动" in document
    assert len(ledger) == 8
    assert summary["headline"]["rtl_iterative_rescues"] == 3
    assert summary["headline"]["parameter_mean_bo_minus_random"] == .02
