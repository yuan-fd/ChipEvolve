from openroad_platform_analysis.v2_research import v2_research_protocols


def test_v2_research_protocols_cover_claims_with_failure_aware_repetitions():
    protocols = v2_research_protocols()
    assert set(protocols) == {"rtl", "parameter", "learning"}
    assert all(item["repetitions"] >= 3 for item in protocols.values())
    assert "independent" in protocols["rtl"]["arms"]
    assert "causal" in protocols["learning"]["arms"]
