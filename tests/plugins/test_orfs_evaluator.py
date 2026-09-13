"""ORFS signoff gates.

Every test here is a way a run could be reported as better than it is, or worse
than it is.  The gates are quoted from the frozen v1 implementation, so these
tests are the specification for what "admissible" means.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from stage_json import METRIC_SPECS

from evaluator import (
    CLAIM_BOUNDARY,
    REQUIRED_FINAL_ARTIFACTS,
    REQUIRED_METRICS,
    evaluate_orfs_run,
    write_immutable_evaluation,
)

SHA_A = "a" * 64
SHA_B = "b" * 64


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def build_run(
    root: Path, *, time_unit: str = "1ns", scale: float = 1.0,
    setup_ws: float = 0.1, hold_ws: float = 0.05, drc: int = 0,
    omit_stage: str | None = None, omit_metrics: tuple[str, ...] = (),
    drop_artifacts: tuple[str, ...] = (),
) -> tuple[Path, Path]:
    """A complete six-stage ORFS run, with one thing wrong if asked.

    ``scale`` is applied to the reported timing values so a picosecond platform
    can be modelled honestly: the numbers are large and the unit says so.
    """
    logs = root / "logs" / "platform" / "design" / "base"
    results = root / "results" / "platform" / "design" / "base"
    logs.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)

    stages = {
        "1_1_yosys.json": {
            "synth__design__instance__count": 4200,
            "synth__design__instance__area": 12345.6789,
            "synth__design__io": 42,
            "run__flow__platform__time_units": time_unit,
        },
        "2_1_floorplan.json": {
            "floorplan__design__die__area": 250000.0,
            "floorplan__design__core__area": 200000.0,
            "floorplan__design__instance__utilization": 0.6,
        },
        "3_5_place_dp.json": {
            "detailedplace__design__instance__count": 4200,
            "detailedplace__design__instance__area": 12345.6789,
        },
        "4_1_cts.json": {
            "cts__clock__skew__worst": 0.03 * scale,
            "cts__timing__setup__ws": setup_ws * scale,
        },
        "5_2_route.json": {
            "detailedroute__route__wirelength": 456789,
            "detailedroute__route__vias": 98765,
            "detailedroute__route__drc_errors": drc,
            "detailedroute__timing__setup__ws": setup_ws * scale,
            "detailedroute__timing__hold__ws": hold_ws * scale,
        },
        "6_report.json": {
            "finish__design__instance__count": 4200,
            "finish__design__instance__area": 12345.6789,
            "finish__design__die__area": 250000.0,
            "finish__design__core__area": 200000.0,
            "finish__design__instance__utilization": 0.62,
            "finish__timing__setup__ws": setup_ws * scale,
            "finish__timing__hold__ws": hold_ws * scale,
            "finish__power__total": 0.0123,
        },
    }
    # A terminal metric is legitimately available from more than one stage
    # (detailed route reports timing as well as the final report), so omitting
    # it means removing every candidate key in every stage.  Removing only the
    # final-report copy would leave the metric present and prove nothing.
    canonical_for_short = {
        "area": "instance_area_um2", "setup": "setup_wns_ns",
        "power": "power_W", "drc": "drc_errors",
    }
    drop_suffixes: set[str] = set()
    for short in omit_metrics:
        canonical = canonical_for_short[short]
        for specs in METRIC_SPECS.values():
            for name, candidates in specs:
                if name == canonical:
                    drop_suffixes.update(candidates)

    def dropped(key: str) -> bool:
        stripped = key.split("__", 1)[-1]
        return any(stripped.endswith(suffix) for suffix in drop_suffixes)

    stage_names = {"1": "synth", "2": "floorplan", "3": "place",
                   "4": "cts", "5": "route", "6": "finish"}
    for filename, payload in stages.items():
        if omit_stage == stage_names[filename.split("_")[0]]:
            continue
        if drop_suffixes:
            payload = {k: v for k, v in payload.items() if not dropped(k)}
        write_json(logs / filename, payload)

    for name in REQUIRED_FINAL_ARTIFACTS:
        if name in drop_artifacts:
            continue
        (results / name).write_text(f"content of {name}\n", encoding="utf-8")
    return logs, results


def evaluate(logs: Path, results: Path, **overrides):
    base = dict(
        log_dir=logs, result_dir=results, platform="sky130hd", design="gcd",
        design_identity_sha256=SHA_A, effective_config_sha256=SHA_B, or_seed=1,
        source_kind="native-platform-orfs", clock_period_ns=2.0,
        runtime_seconds=123.5,
    )
    base.update(overrides)
    return evaluate_orfs_run(**base)


# --------------------------------------------------------------------------
# the honest run
# --------------------------------------------------------------------------

def test_a_clean_run_is_feasible(tmp_path):
    logs, results = build_run(tmp_path)
    evaluation = evaluate(logs, results)
    assert evaluation["feasible"] is True
    assert evaluation["gate"]["status"] == "passed"
    assert evaluation["gate"]["reasons"] == []
    assert evaluation["metrics"]["setup_wns_ns"] == pytest.approx(0.1)
    assert evaluation["metrics"]["drc_errors"] == 0
    assert evaluation["metrics"]["runtime_seconds"] == 123.5


def test_the_evaluation_states_its_own_limits(tmp_path):
    """A passing gate is not a correctness proof, and the artifact says so."""
    logs, results = build_run(tmp_path)
    evaluation = evaluate(logs, results)
    assert evaluation["claim_boundary"] == CLAIM_BOUNDARY
    assert "does not prove RTL functional correctness" in evaluation["claim_boundary"]


def test_an_identical_run_produces_an_identical_evaluation_id(tmp_path):
    """The id is what makes two runs comparable, so it must be reproducible."""
    logs, results = build_run(tmp_path)
    first = evaluate(logs, results)
    second = evaluate(logs, results)
    assert first["evaluation_id"] == second["evaluation_id"]


def test_a_different_seed_is_a_different_evaluation(tmp_path):
    logs, results = build_run(tmp_path)
    assert evaluate(logs, results)["evaluation_id"] != \
        evaluate(logs, results, or_seed=2)["evaluation_id"]


def test_a_different_source_kind_changes_identity_but_not_the_metrics(tmp_path):
    """Provenance is labelled, never scored differently."""
    logs, results = build_run(tmp_path)
    native = evaluate(logs, results, source_kind="native-platform-orfs")
    upstream = evaluate(logs, results, source_kind="upstream-autotuner")
    assert native["metrics"] == upstream["metrics"]
    assert native["feasible"] == upstream["feasible"]
    assert native["evaluation_id"] != upstream["evaluation_id"]


# --------------------------------------------------------------------------
# the gates
# --------------------------------------------------------------------------

def test_a_missing_final_artifact_fails_the_gate(tmp_path):
    logs, results = build_run(tmp_path, drop_artifacts=("6_final.gds",))
    evaluation = evaluate(logs, results)
    assert evaluation["feasible"] is False
    assert "missing_final_artifacts" in evaluation["gate"]["reasons"]
    assert evaluation["gate"]["missing_artifacts"] == ["6_final.gds"]


def test_an_empty_final_artifact_does_not_count(tmp_path):
    logs, results = build_run(tmp_path)
    (results / "6_final.def").write_text("", encoding="utf-8")
    evaluation = evaluate(logs, results)
    assert evaluation["feasible"] is False
    assert "6_final.def" in evaluation["gate"]["missing_artifacts"]


def test_an_incomplete_flow_fails_the_gate(tmp_path):
    logs, results = build_run(tmp_path, omit_stage="cts")
    evaluation = evaluate(logs, results)
    assert evaluation["feasible"] is False
    assert "incomplete_stage_json" in evaluation["gate"]["reasons"]


def test_a_missing_required_metric_fails_the_gate(tmp_path):
    logs, results = build_run(tmp_path, omit_metrics=("power",))
    evaluation = evaluate(logs, results)
    assert evaluation["feasible"] is False
    assert "missing_required_metrics" in evaluation["gate"]["reasons"]
    assert evaluation["gate"]["missing_metrics"] == ["power_W"]
    # A missing metric is null, never zero: zero would mean "we measured none".
    assert evaluation["metrics"]["power_W"] is None


def test_every_required_metric_is_actually_required(tmp_path):
    for short in ("area", "setup", "power", "drc"):
        root = tmp_path / short
        logs, results = build_run(root, omit_metrics=(short,))
        evaluation = evaluate(logs, results)
        assert evaluation["feasible"] is False, short
        assert "missing_required_metrics" in evaluation["gate"]["reasons"], short


def test_route_stage_timing_is_an_acceptable_source(tmp_path):
    """Detailed route reports timing too, and that is not a substitution.

    If only the final report carried the value, a run whose 6_report was lost
    would be scored as missing rather than as measured.
    """
    logs, results = build_run(tmp_path)
    write_json(logs / "6_report.json", {
        "finish__design__instance__area": 12345.6789,
        "finish__power__total": 0.0123,
    })
    evaluation = evaluate(logs, results)
    assert evaluation["metrics"]["setup_wns_ns"] == pytest.approx(0.1)


def test_an_unverified_time_unit_fails_the_gate(tmp_path):
    """Without a unit, every timing number has unknown scale.

    Accepting such a run would let a picosecond run be compared against a
    nanosecond run as though the numbers meant the same thing.
    """
    logs, results = build_run(tmp_path)
    write_json(logs / "1_1_yosys.json", {"synth__design__instance__count": 1})
    evaluation = evaluate(logs, results)
    assert evaluation["feasible"] is False
    assert "unverified_time_unit" in evaluation["gate"]["reasons"]


def test_a_setup_timing_violation_fails_the_gate(tmp_path):
    logs, results = build_run(tmp_path, setup_ws=-0.05)
    evaluation = evaluate(logs, results)
    assert evaluation["feasible"] is False
    assert "setup_timing_violation" in evaluation["gate"]["reasons"]


def test_a_hold_timing_violation_fails_the_gate(tmp_path):
    logs, results = build_run(tmp_path, hold_ws=-0.01)
    evaluation = evaluate(logs, results)
    assert evaluation["feasible"] is False
    assert "hold_timing_violation" in evaluation["gate"]["reasons"]


def test_a_nonzero_drc_fails_the_gate(tmp_path):
    logs, results = build_run(tmp_path, drc=3)
    evaluation = evaluate(logs, results)
    assert evaluation["feasible"] is False
    assert "drc_violation" in evaluation["gate"]["reasons"]


def test_a_picosecond_run_is_scored_in_nanoseconds(tmp_path):
    """The end-to-end version of the unit trap: an ASAP7 run whose slack is
    0.1 ns is reported as 100 ps, and must still pass the gate."""
    logs, results = build_run(tmp_path, time_unit="1ps", scale=1000.0,
                              setup_ws=0.1, hold_ws=0.05)
    evaluation = evaluate(logs, results, platform="asap7")
    assert evaluation["metrics"]["setup_wns_ns"] == pytest.approx(0.1)
    assert evaluation["feasible"] is True, evaluation["gate"]["reasons"]


def test_a_picosecond_run_with_a_real_violation_still_fails(tmp_path):
    """The conversion must not accidentally turn a violation into a pass."""
    logs, results = build_run(tmp_path, time_unit="1ps", scale=1000.0,
                              setup_ws=-0.05)
    evaluation = evaluate(logs, results, platform="asap7")
    assert evaluation["metrics"]["setup_wns_ns"] == pytest.approx(-0.05)
    assert evaluation["feasible"] is False
    assert "setup_timing_violation" in evaluation["gate"]["reasons"]


# --------------------------------------------------------------------------
# input validation
# --------------------------------------------------------------------------

def test_source_kind_is_required(tmp_path):
    logs, results = build_run(tmp_path)
    with pytest.raises(ValueError, match="source_kind is required"):
        evaluate(logs, results, source_kind="  ")


def test_an_invalid_identity_digest_is_refused(tmp_path):
    logs, results = build_run(tmp_path)
    with pytest.raises(ValueError, match="design_identity_sha256"):
        evaluate(logs, results, design_identity_sha256="not-a-digest")
    with pytest.raises(ValueError, match="effective_config_sha256"):
        evaluate(logs, results, effective_config_sha256="xyz")


def test_a_negative_seed_is_refused(tmp_path):
    logs, results = build_run(tmp_path)
    with pytest.raises(ValueError, match="or_seed"):
        evaluate(logs, results, or_seed=-1)
    with pytest.raises(ValueError, match="or_seed"):
        evaluate(logs, results, or_seed=True)  # bool is not an int here


# --------------------------------------------------------------------------
# immutability
# --------------------------------------------------------------------------

def test_an_identical_rewrite_is_idempotent(tmp_path):
    logs, results = build_run(tmp_path)
    evaluation = evaluate(logs, results)
    target = tmp_path / "evaluation.json"
    first = write_immutable_evaluation(target, evaluation)
    second = write_immutable_evaluation(target, evaluation)
    assert first == second
    assert json.loads(target.read_text(encoding="utf-8"))["evaluation_id"] == \
        evaluation["evaluation_id"]


def test_a_different_rewrite_is_refused(tmp_path):
    """Evidence that can be silently rewritten is not evidence."""
    logs, results = build_run(tmp_path)
    target = tmp_path / "evaluation.json"
    write_immutable_evaluation(target, evaluate(logs, results))
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        write_immutable_evaluation(target, evaluate(logs, results, or_seed=99))


def test_the_evaluation_records_every_artifact_hash(tmp_path):
    logs, results = build_run(tmp_path)
    evaluation = evaluate(logs, results)
    by_name = {a["name"]: a for a in evaluation["artifacts"]}
    assert set(by_name) == set(REQUIRED_FINAL_ARTIFACTS)
    for name, entry in by_name.items():
        expected = hashlib.sha256((results / name).read_bytes()).hexdigest()
        assert entry["sha256"] == expected
        assert entry["size_bytes"] > 0


def test_the_required_tables_are_what_the_gate_claims(tmp_path):
    """The gate's own rule text must match the constants it enforces."""
    logs, results = build_run(tmp_path)
    rules = evaluate(logs, results)["gate"]["rules"]
    assert rules["required_final_artifacts"] == list(REQUIRED_FINAL_ARTIFACTS)
    assert rules["required_metrics"] == list(REQUIRED_METRICS)
