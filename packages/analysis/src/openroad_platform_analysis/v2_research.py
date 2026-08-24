"""Frozen v2 research protocols: RTL, parameter search, and learning ablations."""
from __future__ import annotations
from .paper_harness import preregister_protocol


def v2_research_protocols() -> dict[str, dict]:
    """Return fixed protocols; changing one creates a new digest, never a rewrite."""
    common = {"runs": 256, "wall_clock_hours": 168}
    return {
        "rtl": preregister_protocol(
            study_id="v2-rtl-independent-verification", question="Does independent verification improve functional RTL acceptance?",
            designs=("gcd", "fifo", "uart_tx", "ibex_alu"),
            arms={"direct": {"generator": "direct-llm", "verifier": "none"},
                  "rtlscout": {"generator": "rtlscout", "verifier": "frozen_tb"},
                  "independent": {"generator": "rtlscout", "verifier": "separate_tb_sva_mutation"}},
            metrics={"functional_pass_rate": "max", "mutation_score": "max", "area_um2": "min", "setup_wns_ns": "max"},
            repetitions=5, budget=common, stopping_rule="all four designs and five generation seeds per arm"),
        "parameter": preregister_protocol(
            study_id="v2-parameter-search", question="Does constrained BO/GP use fewer ORFS evaluations than fixed baselines?",
            designs=("gcd", "fifo", "uart_tx", "ibex_alu"),
            arms={"default": {"policy": "ORFS default"}, "random": {"policy": "seeded random"},
                  "bo": {"policy": "constrained multiobjective BO"}, "agent": {"policy": "evidence-guided BO/GP"}},
            metrics={"area_um2": "min", "setup_wns_ns": "max", "power_W": "min", "drc_errors": "min", "runtime_seconds": "min"},
            repetitions=3, budget=common, stopping_rule="equal evaluation budget; DRC=0 and WNS>=0 hard gates"),
        "learning": preregister_protocol(
            study_id="v2-learning-transfer", question="Do causal filtering and held-out validation improve transferable recommendations?",
            designs=("gcd", "fifo", "uart_tx", "ibex_alu"),
            arms={"no_memory": {"memory": "none"}, "rag_only": {"memory": "retrieval"},
                  "observed": {"memory": "observed BO"}, "causal": {"memory": "observed plus factorial and holdout"}},
            metrics={"heldout_qor_gain": "max", "transfer_success_rate": "max", "false_rule_rate": "min", "recommendation_cost": "min"},
            repetitions=3, budget=common, stopping_rule="leave-one-design-out; rule promotion requires controlled holdout"),
    }
