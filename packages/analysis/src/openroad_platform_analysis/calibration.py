"""Deterministic GP calibration, bounded benchmark sampling, and OOD checks."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

from .optimization import GaussianProcessRegressorLite


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class CalibrationReport:
    report_id: str
    sample_count: int
    held_out_rmse: float
    normalized_rmse: float
    interval_coverage: float
    interval_level: float
    residual_scale: float
    model_id: str
    split: str = "leave-one-out"
    predictions_are_observations: bool = False

    def validate(self) -> None:
        if self.sample_count < 3 or self.held_out_rmse < 0 or self.normalized_rmse < 0:
            raise ValueError("Invalid calibration report")
        if not 0 <= self.interval_coverage <= 1 or not 0 < self.interval_level < 1:
            raise ValueError("Invalid interval calibration")
        if self.predictions_are_observations:
            raise ValueError("Predictions cannot be observations")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class OODAssessment:
    ood: bool
    bounded: bool
    nearest_normalized_distance: float
    predictive_stddev: float
    threshold: float
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


def bounded_benchmark_points(bounds: Mapping[str, tuple[float, float]], *,
                             count: int, seed: int) -> tuple[dict[str, float], ...]:
    """Generate replayable Latin-hypercube points; this function never executes them."""
    if not 3 <= count <= 64 or not bounds or len(bounds) > 16:
        raise ValueError("Benchmark point request is outside policy")
    names = tuple(sorted(bounds))
    for low, high in bounds.values():
        if not np.isfinite([low, high]).all() or low >= high:
            raise ValueError("Invalid benchmark bound")
    rng = np.random.default_rng(seed)
    columns = []
    for _ in names:
        values = (np.arange(count) + rng.random(count)) / count
        rng.shuffle(values)
        columns.append(values)
    rows = []
    for index in range(count):
        row = {}
        for name, column in zip(names, columns):
            low, high = bounds[name]
            row[name] = float(low + column[index] * (high - low))
        rows.append(row)
    return tuple(rows)


def calibrate_gp(x: np.ndarray, y: np.ndarray, *, interval_level: float = 0.95) -> CalibrationReport:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float).reshape(-1)
    if x.ndim != 2 or len(x) != len(y) or len(y) < 3 or not np.isfinite(x).all() \
            or not np.isfinite(y).all():
        raise ValueError("Calibration requires at least three finite aligned samples")
    if not 0.8 <= interval_level < 1:
        raise ValueError("Calibration interval must be between 0.8 and 1")
    predictions, sigmas = [], []
    for held_out in range(len(y)):
        keep = np.arange(len(y)) != held_out
        model = GaussianProcessRegressorLite().fit(x[keep], y[keep])
        mean, stddev = model.predict(x[held_out:held_out + 1])
        predictions.append(float(mean[0]))
        sigmas.append(float(stddev[0]))
    residuals = y - np.asarray(predictions)
    rmse = float(np.sqrt(np.mean(residuals ** 2)))
    scale = max(float(np.ptp(y)), float(np.std(y)), 1e-12)
    # 1.96 is deliberately explicit: the platform reports empirical coverage,
    # and does not pretend the small-data residuals are perfectly Gaussian.
    half_width = 1.96 * np.maximum(np.asarray(sigmas), 1e-12)
    coverage = float(np.mean(np.abs(residuals) <= half_width))
    residual_scale = float(np.quantile(np.abs(residuals), interval_level))
    payload = {"x": x.tolist(), "y": y.tolist(), "model": GaussianProcessRegressorLite.model_id}
    report = CalibrationReport(
        report_id=f"calibration-{_digest(payload)[:24]}", sample_count=len(y),
        held_out_rmse=rmse, normalized_rmse=rmse / scale,
        interval_coverage=coverage, interval_level=interval_level,
        residual_scale=residual_scale, model_id=GaussianProcessRegressorLite.model_id,
    )
    report.validate()
    return report


def assess_ood(candidate: Sequence[float], observed_x: np.ndarray,
               bounds: Sequence[tuple[float, float]], *, predictive_stddev: float,
               distance_threshold: float = 0.5) -> OODAssessment:
    point = np.asarray(candidate, dtype=float).reshape(-1)
    observed = np.asarray(observed_x, dtype=float)
    if observed.ndim != 2 or observed.shape[1] != len(point) or len(bounds) != len(point):
        raise ValueError("OOD dimensions do not align")
    lows = np.asarray([item[0] for item in bounds], dtype=float)
    highs = np.asarray([item[1] for item in bounds], dtype=float)
    widths = highs - lows
    if np.any(widths <= 0) or predictive_stddev < 0 or distance_threshold <= 0:
        raise ValueError("Invalid OOD policy")
    bounded = bool(np.all(point >= lows) and np.all(point <= highs))
    normalized = (observed - point) / widths
    nearest = float(np.min(np.linalg.norm(normalized, axis=1))) if len(observed) else float("inf")
    reasons = []
    if not bounded:
        reasons.append("candidate is outside configured parameter bounds")
    if nearest > distance_threshold:
        reasons.append("candidate is too far from observed samples")
    if predictive_stddev > 0.5:
        reasons.append("predictive uncertainty exceeds the conservative threshold")
    return OODAssessment(bool(reasons), bounded, nearest, float(predictive_stddev),
                         distance_threshold, tuple(reasons or ("within bounded observed support",)))
