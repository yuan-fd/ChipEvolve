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


def test_causal_reflection_agent_emits_only_a_bounded_non_executable_study(monkeypatch):
    import json
    import subprocess
    from pathlib import Path
    from apps.api.app import _codex_causal_reflection

    def fake_run(command, **kwargs):
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(json.dumps({
            "claim": "Within this run context, higher utilization may increase congestion.",
            "mechanism": "less routing whitespace raises local demand.",
            "proposed_intervention": {"core_utilization_pct": [30, 40], "repetitions": 2},
            "uncertainty": "Only one design is observed.",
            "falsifier": "A controlled 2x2 study shows no directional difference.",
        }))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("apps.api.app.shutil.which", lambda _name: "/fake/codex")
    monkeypatch.setattr("apps.api.app.subprocess.run", fake_run)
    result = _codex_causal_reflection([{"kind": "edair_evidence_packet", "facts": [], "loss_manifest": {}}])
    assert result["proposed_intervention"]["repetitions"] == 2
