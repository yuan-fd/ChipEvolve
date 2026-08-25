import importlib.util
from pathlib import Path
import sqlite3


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/analyze_v2_paper_parameter_matrix.py"
SPEC = importlib.util.spec_from_file_location("v2_paper_stats", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_exact_sign_flip_and_holm_are_deterministic():
    test = MODULE._sign_flip_pvalue([1.0, 2.0, 3.0], seed=7)
    assert test["method"] == "exact"
    assert test["draws"] == 8
    assert 0 <= test["p_value"] <= 1
    adjusted = MODULE._holm({"a": .01, "b": .04, "c": .5})
    assert adjusted["a"]["holm_adjusted_p_value"] == .03
    assert adjusted["b"]["holm_adjusted_p_value"] == .08
    assert adjusted["c"]["holm_adjusted_p_value"] == .5


def test_best_curve_keeps_infeasible_cells_and_uses_baseline():
    curve, selected = MODULE._best_curve([
        {"round": 0, "utility": 0.0, "summary": {"eligible": True}},
        {"round": 1, "utility": .2, "summary": {"eligible": False}},
        {"round": 2, "utility": .1, "summary": {"eligible": True}},
    ], -1.0)
    assert curve == [0.0, 0.0, .1]
    assert selected["round"] == 2


def test_seeded_bootstrap_is_reproducible():
    first = MODULE._bootstrap_median([-.1, 0, .1, .2], seed=11, draws=1000)
    second = MODULE._bootstrap_median([-.1, 0, .1, .2], seed=11, draws=1000)
    assert first == second


def test_matrix_runner_freezes_protocol_bytes_before_execution():
    runner = (SCRIPT.parent / "run_v2_paper_parameter_matrix.py").read_text(encoding="utf-8")
    assert 'snapshot.write_bytes(protocol_bytes)' in runner
    assert '"protocol_sha256": protocol_sha256' in runner


def test_objective_profile_replay_selects_measured_tradeoffs():
    baseline = {"eligible": True, "metrics": {
        "area_um2": {"median": 100.0}, "setup_wns_ns": {"median": 1.0},
        "power_W": {"median": 1.0}}}
    smaller = {"eligible": True, "metrics": {
        "area_um2": {"median": 80.0}, "setup_wns_ns": {"median": .8},
        "power_W": {"median": 1.0}}}
    faster = {"eligible": True, "metrics": {
        "area_um2": {"median": 120.0}, "setup_wns_ns": {"median": 1.2},
        "power_W": {"median": 1.0}}}
    replay = MODULE._profile_replay([{"summary": baseline}, {"summary": smaller},
                                     {"summary": faster}])
    assert replay["area"]["selected_index"] == 1
    assert replay["timing"]["selected_index"] == 2
    assert replay["performance"] == replay["timing"]


def test_runtime_rows_preserve_failures_and_registered_order(tmp_path):
    database = tmp_path / "runtime.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE runtime_runs "
                           "(run_id TEXT PRIMARY KEY, status TEXT, started_at TEXT, ended_at TEXT)")
        connection.executemany("INSERT INTO runtime_runs VALUES (?, ?, ?, ?)", [
            ("a", "failed", "2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00"),
            ("b", "succeeded", "2026-01-01T00:00:00+00:00", "2026-01-01T00:02:00+00:00")])

    rows = MODULE._runtime_rows(database, ["b", "a"])

    assert [row["run_id"] for row in rows] == ["b", "a"]
    assert [row["status"] for row in rows] == ["succeeded", "failed"]
