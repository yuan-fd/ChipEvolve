from openroad_platform_analysis import (HypothesisLedger, assess_hypothesis, promote_after_holdout,
                                        reflection_hypothesis)


def test_reflection_requires_controlled_evidence_before_transfer(tmp_path):
    hypothesis = reflection_hypothesis(claim="density and util reduce area together", mechanism="packing",
        context={"design": "gcd"}, producer="diagnosis-agent", proposed_intervention={"util": [30, 50]},
        evidence_refs=[{"ref": "run:r1", "sha256": "a" * 64}])
    assessment = assess_hypothesis(hypothesis, intervention_report={"causal_eligible": True, "interaction_effect": -1.0}, expected_direction="min")
    assert assessment["status"] == "supported"
    assert not promote_after_holdout(assessment, {"eligible": True, "outcome": "rejected"})["promoted"]
    promoted = promote_after_holdout(assessment, {"eligible": True, "outcome": "validated"})
    assert promoted["promoted"]
    ledger = HypothesisLedger(tmp_path / "hypotheses.db")
    ledger.append(hypothesis); ledger.append({**assessment, "claim": hypothesis["claim"], "mechanism": hypothesis["mechanism"]})
    assert len(ledger.history(hypothesis["hypothesis_id"])) == 2
