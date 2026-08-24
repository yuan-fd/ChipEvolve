from openroad_platform_analysis import PaperProtocolStore, compare_arms, preregister_protocol, summarize_arm


def test_protocol_reports_failure_and_variation_before_claiming_improvement(tmp_path):
    protocol = preregister_protocol(study_id="rtl-ablation-v1", question="does independent verification help?",
        designs=["gcd", "uart"], arms={"baseline": {"method": "direct"}, "verified": {"method": "separate-verifier"}},
        metrics={"area": "min"}, repetitions=3, budget={"runs": 30}, stopping_rule="fixed 3 repetitions")
    base = summarize_arm(protocol, arm="baseline", design="gcd", metric="area", values=[10, 10.2, 9.9], terminal_statuses=["succeeded"] * 3)
    candidate = summarize_arm(protocol, arm="verified", design="gcd", metric="area", values=[8.8, 9.0, 8.9], terminal_statuses=["succeeded"] * 3)
    result = compare_arms(base, candidate, minimum_relative_improvement=.05)
    assert result["eligible"] and base["bootstrap_median_95ci"]
    store = PaperProtocolStore(str(tmp_path / "paper.db"))
    assert store.get(store.add(protocol))["study_id"] == "rtl-ablation-v1"
