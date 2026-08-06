from __future__ import annotations

import numpy as np

from openroad_platform_analysis import (
    assess_ood, bounded_benchmark_points, calibrate_gp, research_method_catalog,
)


def test_four_cited_methods_map_to_real_platform_symbols_without_execution_authority():
    catalog = research_method_catalog()
    assert len(catalog["methods"]) == 4
    assert {item["doi"] for item in catalog["methods"]} == {
        "10.1109/TCAD.2022.3167858", "10.1145/3676536.3676730",
        "10.1109/ASP-DAC47756.2020.9045559", "10.3390/electronics9040685",
    }
    assert catalog["execution_authority"] == "Workflow Runtime"
    assert all(item["implementation"] for item in catalog["methods"])


def test_benchmark_sampling_is_bounded_deterministic_and_data_only():
    bounds = {"core_utilization_pct": (20.0, 60.0), "place_density": (0.3, 0.8)}
    first = bounded_benchmark_points(bounds, count=8, seed=19)
    second = bounded_benchmark_points(bounds, count=8, seed=19)
    assert first == second and len(first) == 8
    assert all(20 <= row["core_utilization_pct"] <= 60 for row in first)
    assert all(0.3 <= row["place_density"] <= 0.8 for row in first)


def test_gp_leave_one_out_calibration_and_ood_are_explicit():
    x = np.array([[0.0], [0.2], [0.4], [0.6], [0.8], [1.0]])
    y = (x[:, 0] - 0.45) ** 2
    report = calibrate_gp(x, y)
    assert report.sample_count == 6
    assert report.predictions_are_observations is False
    assert 0 <= report.interval_coverage <= 1
    inside = assess_ood([0.42], x, [(0.0, 1.0)], predictive_stddev=0.1)
    outside = assess_ood([1.4], x, [(0.0, 1.0)], predictive_stddev=0.8)
    assert inside.ood is False
    assert outside.ood is True and outside.bounded is False
