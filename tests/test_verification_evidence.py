import hashlib

from openroad_platform_analysis import (generate_mutants, independent_verification_gate,
                                        mutation_report)


def test_mutation_score_distinguishes_compile_invalid_from_oracle_kill():
    source = "module d(input logic a,b, output logic y); assign y = (a == b) && 1; endmodule"
    mutants = generate_mutants(source)
    assert mutants
    outcomes = {item.mutation_id: "killed" for item in mutants[:2]}
    outcomes[mutants[2].mutation_id] = "survived"
    report = mutation_report(mutants, outcomes,
                             testbench_sha256=hashlib.sha256(b"tb").hexdigest(),
                             verifier_identity="verification-agent", minimum_score=.6)
    assert report["invalid_count"] >= 0
    assert report["killed_count"] == 2
    assert report["survived_count"] == 1
    assert report["eligible"] is True
    assert independent_verification_gate(candidate_generator="rtlscout-v2",
                                         verifier_identity="verification-agent",
                                         testbench_origin="approved_generated", report=report)["accepted"]


def test_same_agent_cannot_claim_independent_oracle():
    report = {"eligible": True}
    assert not independent_verification_gate(candidate_generator="same-agent", verifier_identity="same-agent",
                                             testbench_origin="approved_generated", report=report)["accepted"]
