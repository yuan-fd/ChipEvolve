"""The closed registry of ORFS design recipe options.

These three switches are not tuning knobs: they describe how a design must be
elaborated.  The registry is closed on purpose.  The configuration writer emits
``export <name> = <value>`` lines, so an option name that reached it unchecked
would be able to set any Make variable the flow reads -- a task bundle would be
able to smuggle an arbitrary assignment into the execution environment.
"""

from __future__ import annotations

import pytest

from design_options import (
    BOOL_OPTIONS,
    design_option_config_lines,
    validate_design_options,
)

# --------------------------------------------------------------------------
# the registry
# --------------------------------------------------------------------------

def test_every_option_maps_to_an_orfs_make_variable():
    assert BOOL_OPTIONS == {
        "openroad_hierarchical": "OPENROAD_HIERARCHICAL",
        "swap_arith_operators": "SWAP_ARITH_OPERATORS",
        "remove_abc_buffers": "REMOVE_ABC_BUFFERS",
    }


def test_an_unknown_option_is_refused_by_name():
    """Dropping it would elaborate a different design than the one requested."""
    with pytest.raises(ValueError, match="unknown ORFS design recipe options: evil"):
        validate_design_options({"evil": 1})


def test_an_unknown_option_cannot_be_smuggled_beside_a_real_one():
    with pytest.raises(ValueError, match="unknown ORFS design recipe options: .*pdn"):
        validate_design_options({"remove_abc_buffers": 1, "pdn_extra": 1})


# --------------------------------------------------------------------------
# the values
# --------------------------------------------------------------------------

def test_a_boolean_is_accepted_in_both_spellings():
    assert validate_design_options({"remove_abc_buffers": True}) == {
        "remove_abc_buffers": 1}
    assert validate_design_options({"remove_abc_buffers": 1}) == {
        "remove_abc_buffers": 1}
    assert validate_design_options({"remove_abc_buffers": False}) == {
        "remove_abc_buffers": 0}


def test_a_string_that_looks_like_a_boolean_is_refused():
    """``"1"`` would reach the flow as ``export X = 1`` and look identical."""
    with pytest.raises(ValueError, match="must be boolean"):
        validate_design_options({"swap_arith_operators": "1"})


def test_an_out_of_range_integer_is_refused():
    with pytest.raises(ValueError, match="must be boolean"):
        validate_design_options({"openroad_hierarchical": 2})


def test_no_options_is_an_empty_set_not_an_error():
    assert validate_design_options(None) == {}
    assert validate_design_options({}) == {}


# --------------------------------------------------------------------------
# the config lines
# --------------------------------------------------------------------------

def test_the_lines_are_the_orfs_variables():
    assert design_option_config_lines({"remove_abc_buffers": 1}) == (
        "export REMOVE_ABC_BUFFERS = 1",
    )


def test_the_lines_are_sorted_so_two_runs_agree():
    """Byte-identical configuration is what makes the request fingerprint mean
    anything: otherwise the same recipe could hash to two different configs."""
    lines = design_option_config_lines({
        "swap_arith_operators": 1, "openroad_hierarchical": 1,
        "remove_abc_buffers": 1,
    })
    assert lines == (
        "export OPENROAD_HIERARCHICAL = 1",
        "export REMOVE_ABC_BUFFERS = 1",
        "export SWAP_ARITH_OPERATORS = 1",
    )


def test_a_false_option_is_written_rather_than_omitted():
    """An explicit 0 is a decision; an absent key is silence.  The flow's own
    default is not the platform's request."""
    assert design_option_config_lines({"remove_abc_buffers": 0}) == (
        "export REMOVE_ABC_BUFFERS = 0",
    )
