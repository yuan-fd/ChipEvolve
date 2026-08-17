"""Neighbor parameter grid generation for batch-parallel (L1) campaigns.

Turns a baseline parameter set into a small grid of *adjacent* candidates
(around the baseline values) so users can launch a batch run with one click.
"""

from __future__ import annotations

from typing import Any, Mapping

GRID_PARAMETERS: dict[str, tuple[float, float]] = {
    "clock_period_ns": (0.01, 1000.0),
    "core_utilization_pct": (1.0, 99.0),
    "place_density": (0.01, 1.0),
    "stage_timeout_seconds": (1.0, 86_400.0),
}

_DEFAULTS = {"core_utilization_pct": 45.0, "place_density": 0.45}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _lin(base: float, step: float, n: int, lo: float, hi: float) -> tuple[float, ...]:
    """n values around base: base + step*i for i in a centered window, clamped."""
    raw = {round(_clamp(base + step * i, lo, hi), 4)
           for i in range(-(n // 2), n // 2 + 1)}
    raw.add(round(_clamp(base, lo, hi), 4))
    if len(raw) <= n:
        return tuple(sorted(raw))
    # keep the n values closest to the baseline, then sort them
    chosen = sorted(raw, key=lambda v: abs(v - base))[:n]
    return tuple(sorted(chosen))


def generate_neighbor_grid(
    base_parameters: Mapping[str, Any] | None = None, *,
    count: int = 6,
    utilization_step: float = 5.0,
    density_step: float = 0.05,
) -> dict[str, list[float]]:
    """Build a parameter grid of ~``count`` neighboring candidates.

    ``count`` must be in [2, 16]. The grid uses ``core_utilization_pct`` and
    ``place_density`` (the two most common physical-design knobs); each
    candidate differs from the baseline by one small step. The baseline value
    itself is always included.
    """
    if not 2 <= count <= 16:
        raise ValueError("neighbor candidate count must be between 2 and 16")
    base = dict(base_parameters or {})
    base_util = float(base.get("core_utilization_pct", _DEFAULTS["core_utilization_pct"]))
    base_density = float(base.get("place_density", _DEFAULTS["place_density"]))
    util_lo, util_hi = GRID_PARAMETERS["core_utilization_pct"]
    dens_lo, dens_hi = GRID_PARAMETERS["place_density"]

    # split count into n1 (utilization) x n2 (density) near the product count
    if count <= 3:
        n1, n2 = count, 1
    elif count <= 6:
        n1, n2 = 3, 2
    elif count <= 8:
        n1, n2 = 4, 2
    else:
        n1, n2 = 4, 3

    utils = _lin(base_util, utilization_step, n1, util_lo, util_hi)
    densities = _lin(base_density, density_step, n2, dens_lo, dens_hi)
    grid: dict[str, list[float]] = {"core_utilization_pct": list(utils)}
    if n2 > 1:
        grid["place_density"] = list(densities)
    return grid
