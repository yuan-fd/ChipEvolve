import hashlib

from openroad_platform_analysis import agent_evidence_view, build_edair, physical_ir, timing_ir


def _source():
    return {"artifact_id": "artifact-report", "sha256": hashlib.sha256(b"report").hexdigest(),
            "kind": "report", "parser": "test", "parser_version": "1", "source_size_bytes": 6}


def test_edair_keeps_normalized_facts_linked_to_raw_artifacts():
    source = _source()
    timing = timing_ir([{"path_id": "p0", "path_type": "setup", "slack_ns": -0.1,
                         "points": [{"instance": "u0", "increment_ns": .2}]}], source=source)
    physical = physical_ir(instances=[{"name": "u0", "x": 1, "y": 2}], nets=[],
                           violations=[{"rule": "M1 spacing", "x": 2, "y": 3}], source=source)
    run = {"kind": "run_evidence_ir", "fingerprint": "a" * 64}
    edair = build_edair(design=None, run=run, timing=timing, physical=physical, raw_artifacts=[source])
    view = agent_evidence_view(edair)
    assert edair["raw_artifacts"][0]["sha256"] == source["sha256"]
    assert view["facts"][0]["evidence"]["artifact_id"] == "artifact-report"
    assert view["execution_allowed"] is False
