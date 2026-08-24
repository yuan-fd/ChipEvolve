from openroad_platform_analysis import compare_replication_reports, replication_report


def _view(value, *, run_id="r", knob=1, status="succeeded"):
    return {"run": {"run_id": run_id, "status": status, "task_spec": {
        "task_id": run_id, "project_id": "p", "design_id": "gcd", "plugin_id": "orfs",
        "inputs": {"rtl": {"sha256": "a" * 64}}, "parameters": {"platform": "nangate45", "knob": knob},
        "resources": {}, "timeout_seconds": 1, "max_attempts": 1, "expected_artifacts": [],
        "labels": {"replica_index": run_id}, "schema_version": 1}},
        "stages": [{"attempts": [{"metrics": [{"name": "area", "value": value}]}]}],
    }


def test_replication_requires_same_context_and_reports_variation():
    report = replication_report([_view(10, run_id="a"), _view(12, run_id="b")], "area")
    assert report["comparable"] and report["range"] == 2 and report["median"] == 12
    bad = replication_report([_view(10), _view(11, knob=2)], "area")
    assert not bad["comparable"]


def test_replication_comparison_refuses_noisy_or_weak_claims():
    base = replication_report([_view(100, run_id="a"), _view(101, run_id="b")], "area")
    candidate = replication_report([_view(90, run_id="c"), _view(91, run_id="d")], "area")
    compared = compare_replication_reports(base, candidate, direction="min", minimum_relative_improvement=.05)
    assert compared["eligible"] and compared["relative_improvement"] > .05
