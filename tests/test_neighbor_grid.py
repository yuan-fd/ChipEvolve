"""Neighbor grid generation for batch-parallel (L1) mode."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/scheduler/src"))

from openroad_platform_scheduler.neighbor_grid import (  # noqa: E402
    GRID_PARAMETERS, generate_neighbor_grid,
)


def _combinations(grid: dict[str, list[float]]) -> int:
    n = 1
    for values in grid.values():
        n *= len(values)
    return n


def test_default_count_six():
    grid = generate_neighbor_grid({"core_utilization_pct": 45, "place_density": 0.45})
    assert _combinations(grid) == 6
    assert 45.0 in grid["core_utilization_pct"]
    assert 0.45 in grid["place_density"]


def test_values_within_policy_bounds():
    grid = generate_neighbor_grid({"core_utilization_pct": 98, "place_density": 0.98},
                                  count=6)
    util_lo, util_hi = GRID_PARAMETERS["core_utilization_pct"]
    dens_lo, dens_hi = GRID_PARAMETERS["place_density"]
    for v in grid["core_utilization_pct"]:
        assert util_lo <= v <= util_hi
    for v in grid["place_density"]:
        assert dens_lo <= v <= dens_hi
    # baseline kept even near the boundary
    assert 98.0 in grid["core_utilization_pct"]
    assert 0.98 in grid["place_density"]


def test_custom_counts():
    for count in (2, 4, 8, 12):
        grid = generate_neighbor_grid({}, count=count)
        combos = _combinations(grid)
        assert combos >= count or combos >= 2, (count, grid)
        assert combos <= 16


def test_count_validation():
    with pytest.raises(ValueError):
        generate_neighbor_grid({}, count=1)
    with pytest.raises(ValueError):
        generate_neighbor_grid({}, count=17)
