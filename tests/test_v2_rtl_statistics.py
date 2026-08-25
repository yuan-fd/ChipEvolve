import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/analyze_v2_paper_rtl_matrix.py"
SPEC = importlib.util.spec_from_file_location("rtl_stats", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MODULE)


def test_pass_at_k_is_exact_and_monotonic():
    values = [MODULE._pass_at_k(5, 2, k) for k in range(1, 6)]
    assert values[0] == .4
    assert values[-1] == 1.0
    assert values == sorted(values)
    assert MODULE._pass_at_k(5, 0, 5) == 0.0


def test_first_candidate_requires_every_external_quality_gate():
    checks = [
        {"candidate_id": "c0", "check_kind": "compile_lint", "status": "passed"},
        {"candidate_id": "c0", "check_kind": "simulation", "status": "passed"},
        {"candidate_id": "c0", "check_kind": "mutation_quality", "status": "failed"},
        {"candidate_id": "c1", "check_kind": "compile_lint", "status": "passed"},
        {"candidate_id": "c1", "check_kind": "simulation", "status": "passed"},
        {"candidate_id": "c1", "check_kind": "mutation_quality", "status": "passed"},
        {"candidate_id": "c1", "check_kind": "ppa", "status": "passed"},
    ]

    first = MODULE._full_candidate_gate_result(checks, "c0")
    rescued = MODULE._full_candidate_gate_result(checks, "c1")

    assert first == {"passed": False, "outcomes": {
        "compile_lint": "passed", "simulation": "passed",
        "mutation_quality": "failed", "ppa": "missing"}}
    assert rescued["passed"] is True
