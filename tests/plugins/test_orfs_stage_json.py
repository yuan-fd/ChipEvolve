"""ORFS stage-JSON normalization.

This is ported knowledge, so the tests are the specification.  The most
important one is the time-unit conversion: an implementation that renames
``timing__setup__ws`` to ``setup_wns_ns`` without reading
``run__flow__platform__time_units`` is wrong by a factor of 1000 on ASAP7 and
right on sky130hd, which is the worst possible failure -- it looks correct
until someone switches platform.

The fixture keys are the real ORFS key names, taken from the frozen v1 parser's
candidate tables rather than invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stage_json import (
    KEY_NAMESPACES,
    METRIC_SPECS,
    STAGE_FILES,
    TIME_METRICS,
    extract_metrics_from_log_dir,
)


def write_stage(base: Path, filename: str, payload: dict) -> None:
    base.mkdir(parents=True, exist_ok=True)
    (base / filename).write_text(json.dumps(payload), encoding="utf-8")


def ns_run(base: Path) -> None:
    """A complete, clean sky130hd-style run: nanoseconds, DRC 0, no violations."""
    write_stage(base, "1_1_yosys.json", {
        "synth__design__instance__count": 4200,
        "synth__design__instance__area": 12345.6789,
        "synth__design__io": 42,
        "synth__design__nets": 4300,
        "run__flow__platform__time_units": "1ns",
    })
    write_stage(base, "2_1_floorplan.json", {
        "floorplan__design__die__area": 250000.0,
        "floorplan__design__core__area": 200000.0,
        "floorplan__design__instance__utilization": 0.6,
    })
    write_stage(base, "3_5_place_dp.json", {
        "detailedplace__design__instance__count": 4200,
        "detailedplace__design__instance__area": 12345.6789,
        "detailedplace__design__instance__utilization": 0.62,
        "detailedplace__timing__setup__ws": 0.15,
    })
    write_stage(base, "4_1_cts.json", {
        "cts__clock__skew__worst": 0.03,
        "cts__timing__setup__ws": 0.12,
        "cts__timing__hold__ws": 0.04,
    })
    write_stage(base, "5_2_route.json", {
        "detailedroute__route__wirelength": 456789,
        "detailedroute__route__vias": 98765,
        "detailedroute__route__drc_errors": 0,
        "detailedroute__timing__setup__ws": 0.1,
        "detailedroute__timing__hold__ws": 0.05,
    })
    write_stage(base, "6_report.json", {
        "finish__design__instance__count": 4200,
        "finish__design__instance__area": 12345.6789,
        "finish__design__die__area": 250000.0,
        "finish__design__core__area": 200000.0,
        "finish__design__instance__utilization": 0.62,
        "finish__timing__setup__ws": 0.1,
        "finish__timing__setup__tns": 0.0,
        "finish__timing__hold__ws": 0.05,
        "finish__power__total": 0.0123,
        "finish__flow__warnings__count": 3,
        "finish__flow__errors__count": 0,
    })


# --------------------------------------------------------------------------
# the tables themselves
# --------------------------------------------------------------------------

def test_the_stage_prefix_table_matches_orfs_numbering():
    assert STAGE_FILES == {
        "synth": ("1_",), "floorplan": ("2_",), "place": ("3_",),
        "cts": ("4_",), "route": ("5_",), "finish": ("6_",),
    }


def test_every_time_metric_ends_in_ns_so_the_conversion_covers_it():
    """The conversion rule is 'name ends with _ns'.  That is only sound if every
    canonical time metric is actually named that way."""
    declared = {name for specs in METRIC_SPECS.values() for name, _ in specs}
    assert TIME_METRICS == {n for n in declared if n.endswith("_ns")}
    assert "setup_wns_ns" in TIME_METRICS
    assert "hold_wns_ns" in TIME_METRICS
    assert "skew_ns" in TIME_METRICS
    assert "wirelength_um" not in TIME_METRICS


# --------------------------------------------------------------------------
# bucketing and key normalization
# --------------------------------------------------------------------------

def test_each_stage_is_bucketed_by_its_numeric_filename_prefix(tmp_path):
    ns_run(tmp_path)
    result = extract_metrics_from_log_dir(tmp_path, design="gcd", platform="sky130hd")
    assert {s: v["status"] for s, v in result["stages"].items()} == {
        "synth": "completed", "floorplan": "completed", "place": "completed",
        "cts": "completed", "route": "completed", "finish": "completed",
    }


def test_unrecognized_files_are_ignored_rather_than_guessed(tmp_path):
    ns_run(tmp_path)
    (tmp_path / "notes.json").write_text('{"something": 1}', encoding="utf-8")
    (tmp_path / "9_extra.json").write_text('{"x": 1}', encoding="utf-8")
    result = extract_metrics_from_log_dir(tmp_path, design="gcd", platform="sky130hd")
    assert result["stages"]["finish"]["status"] == "completed"
    assert "something" not in json.dumps(result)


def test_a_stage_namespace_prefix_is_stripped():
    """``synth__design__instance__area`` and ``design__instance__area`` are the
    same fact; the first segment only names the stage."""
    from stage_json import _strip_namespace

    assert _strip_namespace("synth__design__instance__area") == "design__instance__area"
    assert _strip_namespace("detailedroute__route__drc_errors") == "route__drc_errors"
    assert "detailedroute" in KEY_NAMESPACES
    # A leading segment that is not a stage namespace is part of the key.
    assert _strip_namespace("run__flow__platform__time_units") == \
        "run__flow__platform__time_units"


def test_a_renamed_key_still_resolves_by_suffix(tmp_path):
    """An ORFS release that moves a key must degrade, not crash."""
    write_stage(tmp_path, "6_report.json", {
        "finish__design__instance__area": 100.0,
        "finish__some__new__prefix__timing__setup__ws": 0.2,
        "run__flow__platform__time_units": "1ns",
    })
    result = extract_metrics_from_log_dir(tmp_path, design="d", platform="p")
    assert result["stages"]["finish"]["metrics"]["setup_wns_ns"] == 0.2


def test_a_string_number_is_accepted_and_a_non_number_is_skipped(tmp_path):
    write_stage(tmp_path, "6_report.json", {
        "finish__design__instance__area": "1234.5",
        "finish__power__total": "not-a-number",
        "run__flow__platform__time_units": "1ns",
    })
    metrics = extract_metrics_from_log_dir(
        tmp_path, design="d", platform="p"
    )["stages"]["finish"]["metrics"]
    assert metrics["instance_area_um2"] == 1234.5
    assert "power_W" not in metrics


def test_a_json_parse_error_does_not_abort_the_whole_report(tmp_path):
    ns_run(tmp_path)
    (tmp_path / "6_broken.json").write_text("{not json", encoding="utf-8")
    result = extract_metrics_from_log_dir(tmp_path, design="d", platform="p")
    assert result["stages"]["finish"]["metrics"]["setup_wns_ns"] == 0.1


# --------------------------------------------------------------------------
# the time unit -- the reason this module exists
# --------------------------------------------------------------------------

def test_a_nanosecond_run_is_not_rescaled(tmp_path):
    ns_run(tmp_path)
    metrics = extract_metrics_from_log_dir(
        tmp_path, design="gcd", platform="sky130hd"
    )["stages"]["finish"]["metrics"]
    assert metrics["setup_wns_ns"] == 0.1
    assert metrics["hold_wns_ns"] == 0.05


def test_a_picosecond_run_is_converted_to_nanoseconds(tmp_path):
    """ASAP7 reports in ps.  Without the conversion every slack is 1000x wrong,
    and a design that misses timing by 0.15 ns looks like it misses by 150 ns.
    """
    write_stage(tmp_path, "6_report.json", {
        "finish__design__instance__area": 500.0,
        "finish__timing__setup__ws": 150.0,
        "finish__timing__hold__ws": 50.0,
        "finish__power__total": 0.02,
        "run__flow__platform__time_units": "1ps",
    })
    result = extract_metrics_from_log_dir(tmp_path, design="d", platform="asap7")
    metrics = result["stages"]["finish"]["metrics"]
    assert result["units"]["time"]["status"] == "verified"
    assert result["units"]["time"]["scale_to_ns"] == pytest.approx(1e-3)
    assert metrics["setup_wns_ns"] == pytest.approx(0.15)
    assert metrics["hold_wns_ns"] == pytest.approx(0.05)


def test_precision_survives_the_picosecond_conversion(tmp_path):
    """Four decimal places of ps are seven meaningful decimals of ns.  Rounding
    those away would erase evidence that ORFS actually reported."""
    write_stage(tmp_path, "6_report.json", {
        "finish__timing__setup__ws": 150.1234,
        "run__flow__platform__time_units": "1ps",
    })
    metrics = extract_metrics_from_log_dir(
        tmp_path, design="d", platform="asap7"
    )["stages"]["finish"]["metrics"]
    assert metrics["setup_wns_ns"] == pytest.approx(0.1501234)


def test_a_missing_time_unit_is_reported_and_nothing_is_converted(tmp_path):
    write_stage(tmp_path, "6_report.json", {
        "finish__timing__setup__ws": 150.0,
    })
    result = extract_metrics_from_log_dir(tmp_path, design="d", platform="unknown")
    unit = result["units"]["time"]
    assert unit["status"] == "missing"
    assert unit["scale_to_ns"] is None
    # Unconverted on purpose: guessing ns here would be wrong on some platforms
    # and there is no way to tell which.
    assert result["stages"]["finish"]["metrics"]["setup_wns_ns"] == 150.0


def test_conflicting_time_units_across_stages_refuse_to_convert(tmp_path):
    write_stage(tmp_path, "5_2_route.json", {
        "run__flow__platform__time_units": "1ns",
        "detailedroute__timing__setup__ws": 0.1,
    })
    write_stage(tmp_path, "6_report.json", {
        "run__flow__platform__time_units": "1ps",
        "finish__timing__setup__ws": 150.0,
    })
    result = extract_metrics_from_log_dir(tmp_path, design="d", platform="p")
    assert result["units"]["time"]["status"] == "conflict"
    assert set(result["units"]["time"]["raw_values"]) == {"1ns", "1ps"}
    assert result["units"]["time"]["scale_to_ns"] is None


def test_an_unparseable_time_unit_is_rejected_not_guessed(tmp_path):
    write_stage(tmp_path, "6_report.json", {
        "run__flow__platform__time_units": "bananas",
        "finish__timing__setup__ws": 1.0,
    })
    unit = extract_metrics_from_log_dir(
        tmp_path, design="d", platform="p"
    )["units"]["time"]
    assert unit["status"] == "unsupported"
    assert unit["scale_to_ns"] is None


def test_a_magnitude_prefix_is_honoured(tmp_path):
    """``10ps`` is ten picoseconds per unit, not one."""
    write_stage(tmp_path, "6_report.json", {
        "run__flow__platform__time_units": "10ps",
        "finish__timing__setup__ws": 15.0,
    })
    metrics = extract_metrics_from_log_dir(
        tmp_path, design="d", platform="p"
    )["stages"]["finish"]["metrics"]
    assert metrics["setup_wns_ns"] == pytest.approx(0.15)


# --------------------------------------------------------------------------
# percentages and derived values
# --------------------------------------------------------------------------

def test_a_ratio_utilization_becomes_a_percentage(tmp_path):
    write_stage(tmp_path, "2_1_floorplan.json", {
        "floorplan__design__instance__utilization": 0.62,
        "run__flow__platform__time_units": "1ns",
    })
    metrics = extract_metrics_from_log_dir(
        tmp_path, design="d", platform="p"
    )["stages"]["floorplan"]["metrics"]
    assert metrics["utilization_pct"] == pytest.approx(62.0)


def test_a_utilization_already_in_percent_is_left_alone(tmp_path):
    write_stage(tmp_path, "2_1_floorplan.json", {
        "floorplan__design__instance__utilization": 62.0,
    })
    metrics = extract_metrics_from_log_dir(
        tmp_path, design="d", platform="p"
    )["stages"]["floorplan"]["metrics"]
    assert metrics["utilization_pct"] == pytest.approx(62.0)


def test_fmax_is_derived_from_the_clock_period_and_setup_slack(tmp_path):
    write_stage(tmp_path, "6_report.json", {
        "finish__timing__setup__ws": 0.1,
        "run__flow__platform__time_units": "1ns",
    })
    metrics = extract_metrics_from_log_dir(
        tmp_path, design="d", platform="p", clock_period_ns=2.0,
    )["stages"]["finish"]["metrics"]
    # 1000 / (2.0 - 0.1) MHz
    assert metrics["fmax_mhz"] == pytest.approx(round(1000.0 / 1.9, 2))


def test_fmax_is_absent_when_the_period_is_unknown(tmp_path):
    write_stage(tmp_path, "6_report.json", {
        "finish__timing__setup__ws": 0.1,
    })
    metrics = extract_metrics_from_log_dir(
        tmp_path, design="d", platform="p"
    )["stages"]["finish"]["metrics"]
    assert "fmax_mhz" not in metrics


def test_a_zero_or_negative_denominator_does_not_produce_fmax(tmp_path):
    """A slack that meets or exceeds the period means the ratio is meaningless."""
    write_stage(tmp_path, "6_report.json", {
        "finish__timing__setup__ws": 2.5,
        "run__flow__platform__time_units": "1ns",
    })
    metrics = extract_metrics_from_log_dir(
        tmp_path, design="d", platform="p", clock_period_ns=2.0,
    )["stages"]["finish"]["metrics"]
    assert "fmax_mhz" not in metrics


# --------------------------------------------------------------------------
# terminal merge and overall status
# --------------------------------------------------------------------------

def test_an_explicit_route_drc_of_zero_survives_the_terminal_merge(tmp_path):
    """Detailed route owns the DRC count and the final report owns timing.

    Replacing one dictionary with the other loses an explicit DRC of 0 and
    reports a clean run as missing data -- a false negative on the metric people
    care most about.  The fixture deliberately puts DRC only in the route stage
    and timing only in the final report, which is how ORFS actually splits them.
    """
    ns_run(tmp_path)
    result = extract_metrics_from_log_dir(tmp_path, design="d", platform="p")

    route_metrics = result["stages"]["route"]["metrics"]
    finish_metrics = result["stages"]["finish"]["metrics"]
    assert route_metrics["drc_errors"] == 0
    assert "drc_errors" not in finish_metrics  # the split is real in the fixture

    # Both survive, so the run is clean rather than "missing data".
    assert result["summary"]["has_drc_errors"] is False
    assert result["summary"]["overall_status"] == "clean"
    assert result["summary"]["signoff_complete"] is True


def test_a_nonzero_drc_is_a_violation(tmp_path):
    ns_run(tmp_path)
    write_stage(tmp_path, "5_2_route.json", {
        "detailedroute__route__drc_errors": 7,
        "detailedroute__timing__setup__ws": 0.1,
        "run__flow__platform__time_units": "1ns",
    })
    result = extract_metrics_from_log_dir(tmp_path, design="d", platform="p")
    assert result["summary"]["has_drc_errors"] is True
    assert result["summary"]["overall_status"] == "violations"


def test_antenna_violations_also_count_as_physical_violations(tmp_path):
    ns_run(tmp_path)
    write_stage(tmp_path, "5_2_route.json", {
        "detailedroute__route__drc_errors": 0,
        "detailedroute__antenna__violating__nets": 2,
        "run__flow__platform__time_units": "1ns",
    })
    result = extract_metrics_from_log_dir(tmp_path, design="d", platform="p")
    assert result["summary"]["has_drc_errors"] is True


def test_negative_setup_slack_is_a_timing_violation(tmp_path):
    ns_run(tmp_path)
    write_stage(tmp_path, "6_report.json", {
        "finish__timing__setup__ws": -0.05,
        "run__flow__platform__time_units": "1ns",
    })
    result = extract_metrics_from_log_dir(tmp_path, design="d", platform="p")
    assert result["summary"]["has_timing_violation"] is True
    assert result["summary"]["overall_status"] == "violations"


def test_a_missing_stage_makes_the_run_incomplete_not_clean(tmp_path):
    ns_run(tmp_path)
    (tmp_path / "4_1_cts.json").unlink()
    result = extract_metrics_from_log_dir(tmp_path, design="d", platform="p")
    assert result["stages"]["cts"]["status"] == "not_run"
    assert result["summary"]["stages_completed"] == 5
    assert result["summary"]["stages_total"] == 6
    assert result["summary"]["overall_status"] == "incomplete"
    assert result["summary"]["signoff_complete"] is False


def test_signoff_is_only_complete_when_the_final_stage_ran(tmp_path):
    ns_run(tmp_path)
    assert extract_metrics_from_log_dir(
        tmp_path, design="d", platform="p"
    )["summary"]["signoff_complete"] is True
    # Asking for an earlier target stage cannot be signoff.
    partial = extract_metrics_from_log_dir(
        tmp_path, design="d", platform="p", expected_stage="route"
    )
    assert partial["summary"]["signoff_complete"] is False
    assert partial["summary"]["overall_status"] == "violations" or \
        partial["summary"]["overall_status"] == "target_stage_complete"


def test_an_unsupported_expected_stage_is_refused(tmp_path):
    ns_run(tmp_path)
    with pytest.raises(ValueError, match="unsupported expected stage"):
        extract_metrics_from_log_dir(tmp_path, design="d", platform="p",
                                     expected_stage="nonsense")


def test_an_empty_directory_reports_nothing_rather_than_zeroes(tmp_path):
    """'We could not measure' must never look like 'we measured zero'."""
    result = extract_metrics_from_log_dir(tmp_path, design="d", platform="p")
    assert result["summary"]["stages_completed"] == 0
    assert result["summary"]["overall_status"] == "incomplete"
    assert result["summary"]["signoff_complete"] is False
    assert all(stage["metrics"] == {} for stage in result["stages"].values())
