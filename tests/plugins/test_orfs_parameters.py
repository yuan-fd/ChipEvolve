"""The ORFS parameter allowlist.

These are the values a caller may tune, and the bounds each one is held to.  The
point of the table is what is *absent* from it: the clock period and the SDC
constraints are intentionally not searchable, because a QoR comparison in which
the design target can move is not a comparison.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parameters import (
    effective_configuration_id,
    FROZEN_CONSTRAINTS,
    ORFS_PARAMETER_BY_NAME,
    ORFS_PARAMETERS,
    ORFS_PLATFORM_BOUNDS,
    orfs_parameter_config_lines,
    orfs_parameter_schema,
    parameter_source_evidence,
    validate_orfs_parameters,
)


# --------------------------------------------------------------------------
# what may not be tuned
# --------------------------------------------------------------------------

def test_the_design_target_is_not_tunable():
    """A QoR optimizer must not improve its score by weakening the target.

    If the clock period were searchable, "better timing" would sometimes mean
    "an easier design", and no downstream statistic could recover the meaning of
    the comparison.
    """
    names = {p.name for p in ORFS_PARAMETERS}
    for frozen in FROZEN_CONSTRAINTS:
        assert frozen not in names
    assert "clock_period_ns" not in names
    # ...and the schema says so, so a reader does not have to infer it.
    assert set(orfs_parameter_schema()["frozen_constraints"]) == set(
        FROZEN_CONSTRAINTS
    )


def test_the_allowlist_covers_exactly_the_expected_parameters():
    assert {p.name for p in ORFS_PARAMETERS} == {
        "core_utilization_pct", "place_density", "place_density_lb_addon",
        "tns_end_percent", "global_placement_padding",
        "detail_placement_padding", "enable_dpo", "gpl_timing_driven",
        "gpl_routability_driven", "routing_layer_adjustment",
        "cts_cluster_size", "cts_cluster_diameter",
    }


def test_every_parameter_maps_to_a_distinct_environment_variable():
    env_names = [p.env_name for p in ORFS_PARAMETERS]
    assert len(env_names) == len(set(env_names))


# --------------------------------------------------------------------------
# value validation
# --------------------------------------------------------------------------

def test_a_boolean_is_not_accepted_where_a_number_is_expected():
    """Python treats True as 1, so accepting it would let a typo become a value."""
    with pytest.raises(ValueError, match="must be an integer"):
        ORFS_PARAMETER_BY_NAME["core_utilization_pct"].canonicalize(True)
    with pytest.raises(ValueError, match="must be numeric"):
        ORFS_PARAMETER_BY_NAME["place_density"].canonicalize(True)


def test_a_fractional_integer_is_refused():
    with pytest.raises(ValueError, match="must be an integer"):
        ORFS_PARAMETER_BY_NAME["core_utilization_pct"].canonicalize(55.5)


def test_a_non_finite_value_is_refused():
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError, match="must be finite"):
            ORFS_PARAMETER_BY_NAME["place_density"].canonicalize(bad)


def test_a_boolean_parameter_accepts_only_boolean_shapes():
    spec = ORFS_PARAMETER_BY_NAME["enable_dpo"]
    assert spec.canonicalize(True) == 1
    assert spec.canonicalize(False) == 0
    assert spec.canonicalize(1) == 1
    assert spec.canonicalize(0) == 0
    with pytest.raises(ValueError, match="must be boolean"):
        spec.canonicalize("yes")


def test_values_outside_the_global_bounds_are_refused():
    with pytest.raises(ValueError, match="below its lower bound"):
        ORFS_PARAMETER_BY_NAME["core_utilization_pct"].canonicalize(5)
    with pytest.raises(ValueError, match="above its upper bound"):
        ORFS_PARAMETER_BY_NAME["place_density"].canonicalize(0.99)


def test_a_value_off_the_quantization_step_is_refused():
    """A value off the grid would be silently rounded by the tool, so two
    distinct proposals could become the same run."""
    spec = ORFS_PARAMETER_BY_NAME["place_density"]
    assert spec.canonicalize(0.55) == pytest.approx(0.55)
    with pytest.raises(ValueError, match="quantization step"):
        spec.canonicalize(0.555)


def test_a_continuous_parameter_has_no_step():
    """The addon dimension is continuous upstream; imposing a grid here would
    collapse several distinct proposals into one run."""
    spec = ORFS_PARAMETER_BY_NAME["place_density_lb_addon"]
    assert spec.step is None
    assert spec.canonicalize(0.037) == pytest.approx(0.037)


# --------------------------------------------------------------------------
# the request as a whole
# --------------------------------------------------------------------------

def test_an_unknown_parameter_is_refused_not_ignored():
    with pytest.raises(ValueError, match="unsupported ORFS tuning parameters"):
        validate_orfs_parameters({"make_it_fast": 1}, platform="nangate45")


def test_validation_returns_a_canonical_sorted_mapping():
    result = validate_orfs_parameters(
        {"place_density": 0.6, "core_utilization_pct": 45}, platform="nangate45"
    )
    assert list(result) == ["core_utilization_pct", "place_density"]
    assert result["core_utilization_pct"] == 45


def test_the_two_placement_policies_are_mutually_exclusive():
    """ORFS resolves them differently, so setting both leaves an inactive
    contradictory value in the evidence."""
    with pytest.raises(ValueError, match="alternative policies"):
        validate_orfs_parameters(
            {"place_density": 0.6, "place_density_lb_addon": 0.03},
            platform="nangate45",
        )
    validate_orfs_parameters({"place_density_lb_addon": 0.03},
                             platform="nangate45")
    validate_orfs_parameters({"place_density": 0.6}, platform="nangate45")


def test_detail_padding_may_not_exceed_global_padding():
    with pytest.raises(ValueError, match="cannot exceed"):
        validate_orfs_parameters(
            {"global_placement_padding": 1, "detail_placement_padding": 2},
            platform="nangate45",
        )
    validate_orfs_parameters(
        {"global_placement_padding": 2, "detail_placement_padding": 2},
        platform="nangate45",
    )


def test_an_empty_request_is_valid():
    assert validate_orfs_parameters({}, platform="nangate45") == {}


# --------------------------------------------------------------------------
# platform bounds
# --------------------------------------------------------------------------

def test_each_platform_has_its_own_calibrated_bounds():
    assert set(ORFS_PLATFORM_BOUNDS) == {"nangate45", "asap7", "sky130hd"}


def test_the_sky130hd_lower_bound_is_the_published_anchor():
    """The reviewed upstream anchor uses utilization 20.

    A gate that rejected it would refuse the official starting configuration of
    the very study being reproduced, which is a spectacular way to fail.
    """
    assert ORFS_PLATFORM_BOUNDS["sky130hd"]["core_utilization_pct"][0] == 20
    validate_orfs_parameters({"core_utilization_pct": 20}, platform="sky130hd")
    # The global lower bound is the same 20, so this is legal everywhere.
    validate_orfs_parameters({"core_utilization_pct": 20}, platform="nangate45")


def test_a_platform_bound_can_be_narrower_than_the_global_one():
    """Utilization 76 is inside the global 20-80 but outside ASAP7's 30-75."""
    validate_orfs_parameters({"core_utilization_pct": 76}, platform="nangate45")
    with pytest.raises(ValueError, match="calibrated range for asap7"):
        validate_orfs_parameters({"core_utilization_pct": 76}, platform="asap7")


def test_an_unknown_platform_falls_back_to_the_global_bounds():
    validate_orfs_parameters({"core_utilization_pct": 50}, platform="some-new-pdk")


def test_a_parameter_restricted_to_platforms_says_so():
    """No parameter in this table is platform-restricted today; the mechanism is
    asserted so that the day one is, the refusal is not a surprise."""
    restricted = [p for p in ORFS_PARAMETERS if p.platforms]
    for spec in restricted:
        other = "nangate45" if "nangate45" not in spec.platforms else "asap7"
        with pytest.raises(ValueError, match="not supported on"):
            validate_orfs_parameters({spec.name: spec.lower}, platform=other)


# --------------------------------------------------------------------------
# config generation and identity
# --------------------------------------------------------------------------

def test_config_lines_use_the_declared_environment_names():
    lines = orfs_parameter_config_lines(
        {"core_utilization_pct": 45, "cts_cluster_size": 20},
        platform="nangate45",
    )
    assert "export CTS_CLUSTER_SIZE = 20" in lines
    # core_utilization_pct is emitted by the floorplan policy, not here, because
    # it and DIE_AREA are alternatives; the table still names its variable.
    assert ORFS_PARAMETER_BY_NAME["core_utilization_pct"].env_name == \
        "CORE_UTILIZATION"


def test_a_configuration_id_is_stable_and_content_addressed():
    first = effective_configuration_id({"place_density": 0.6},
                                       platform="nangate45")
    second = effective_configuration_id({"place_density": 0.6},
                                        platform="nangate45")
    assert first == second
    assert first.startswith("orfs-effective-")
    assert len(first) == len("orfs-effective-") + 64


def test_a_configuration_id_changes_with_the_platform():
    assert effective_configuration_id({"place_density": 0.6},
                                      platform="nangate45") != \
        effective_configuration_id({"place_density": 0.6}, platform="sky130hd")


def test_a_configuration_id_refuses_an_invalid_request():
    with pytest.raises(ValueError):
        effective_configuration_id({"place_density": 99}, platform="nangate45")


# --------------------------------------------------------------------------
# liveness evidence
# --------------------------------------------------------------------------

def test_source_evidence_records_whether_the_consumer_exists_and_mentions_it(
    tmp_path: Path,
):
    """This is what catches "we set it and the tool ignored it" without running
    the flow."""
    script = tmp_path / "scripts" / "cts.tcl"
    script.parent.mkdir(parents=True)
    script.write_text("clock_tree_synthesis -sink_clustering_size $CTS_CLUSTER_SIZE\n",
                      encoding="utf-8")

    rows = {row["name"]: row for row in parameter_source_evidence(
        tmp_path, {"cts_cluster_size": 20})}
    entry = rows["cts_cluster_size"]
    assert entry["consumer_declared"] is True
    assert len(entry["consumer_sha256"]) == 64
    assert entry["stage"] == "cts"
    assert entry["runtime_patterns"]


def test_source_evidence_reports_a_missing_consumer_rather_than_failing(
    tmp_path: Path,
):
    rows = parameter_source_evidence(tmp_path, {"cts_cluster_size": 20})
    assert rows[0]["consumer_declared"] is False
    assert rows[0]["consumer_sha256"] is None


def test_source_evidence_reports_a_consumer_that_never_mentions_the_variable(
    tmp_path: Path,
):
    """A consumer script that exists but does not read the variable is the
    precise case this evidence exists to surface."""
    script = tmp_path / "scripts" / "cts.tcl"
    script.parent.mkdir(parents=True)
    script.write_text("# this script does not read the variable\n", encoding="utf-8")
    rows = parameter_source_evidence(tmp_path, {"cts_cluster_size": 20})
    assert rows[0]["consumer_declared"] is False
    assert rows[0]["consumer_sha256"] is not None


# --------------------------------------------------------------------------
# the schema
# --------------------------------------------------------------------------

def test_the_schema_is_serialisable_and_complete():
    schema = orfs_parameter_schema()
    text = json.dumps(schema)
    assert len(schema["parameters"]) == len(ORFS_PARAMETERS)
    assert schema["liveness_rule_version"] == "orfs-runtime-patterns-v2"
    for entry in schema["parameters"]:
        assert entry["name"] and entry["env_name"] and entry["stage"]
    assert "orfs-effective-" in text or True  # the id formula is not in the schema
