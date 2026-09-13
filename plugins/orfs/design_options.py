"""Typed, non-searchable design recipe options for ORFS.

Ported from the frozen v1 implementation.  These switches describe *how a design
must be elaborated*; they are not DSE knobs.  Keeping a closed registry is the
point: the configuration writer emits ``export <name> = <value>`` lines, so a
bundle that could name an arbitrary key would be able to set any Make variable
the flow reads.  Validation happens before anything is written, and an unknown
name is refused rather than ignored -- a silently dropped option would mean the
attempt elaborated a different design than the one that was requested.
"""

from __future__ import annotations

from typing import Any, Mapping

#: Accepted option name -> the ORFS Make variable it sets.  Adding a name here is
#: the only way to make a new recipe switch selectable.
BOOL_OPTIONS: dict[str, str] = {
    "openroad_hierarchical": "OPENROAD_HIERARCHICAL",
    "swap_arith_operators": "SWAP_ARITH_OPERATORS",
    "remove_abc_buffers": "REMOVE_ABC_BUFFERS",
}


def validate_design_options(value: Mapping[str, Any] | None) -> dict[str, int]:
    """Return the options as 0/1, or refuse the request naming what is wrong.

    ``True``/``False`` and ``0``/``1`` are both accepted because a recipe is
    written by hand in JSON, where the two spellings mean the same thing.  Every
    other value is an error: a string ``"1"`` reaching the flow as
    ``export X = 1`` would look identical while bypassing the check.
    """
    options = dict(value or {})
    unknown = sorted(set(options) - set(BOOL_OPTIONS))
    if unknown:
        raise ValueError(
            f"unknown ORFS design recipe options: {', '.join(unknown)}")
    validated: dict[str, int] = {}
    for name, raw in options.items():
        if isinstance(raw, bool):
            validated[name] = int(raw)
        elif isinstance(raw, int) and raw in {0, 1}:
            validated[name] = raw
        else:
            raise ValueError(f"ORFS design option {name} must be boolean")
    return validated


def design_option_config_lines(value: Mapping[str, Any] | None) -> tuple[str, ...]:
    """The ``export`` lines for validated options, in a stable order.

    Sorted, so two runs of the same recipe produce byte-identical
    configuration and the fingerprint of the request describes it exactly.
    """
    options = validate_design_options(value)
    return tuple(
        f"export {BOOL_OPTIONS[name]} = {options[name]}" for name in sorted(options)
    )
