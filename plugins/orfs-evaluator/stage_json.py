"""Normalize ORFS per-stage JSON into one canonical metric report.

Ported from the frozen v1 tree, behaviour-preserving.  Every table below was
read out of that implementation rather than reconstructed, because this file is
where real-world knowledge lives and guessing would silently corrupt results.

What it does:

1. Buckets ``*.json`` under a leaf log directory into six ORFS stages by numeric
   filename prefix.
2. Maps each raw key to a canonical metric name using a candidate list, first by
   exact match and then by suffix, so an ORFS release that renames a key does
   not break parsing.
3. Emits one stable structure for the evaluator and any reader.

The correctness trap this file exists to avoid
---------------------------------------------
OpenROAD reports timing in the active Liberty/SDC time unit, and that unit is
**not universally nanoseconds**.  ASAP7 uses picoseconds; sky130hd and nangate45
use nanoseconds.  The authoritative unit is carried in the ORFS JSON under
``run__flow__platform__time_units``.  A parser that merely renames
``timing__setup__ws`` to ``setup_wns_ns`` introduces a 1000x error on ASAP7.
Every time-valued metric is therefore converted before it leaves this module.

Correspondingly, when the unit cannot be read, the module reports that honestly
(``missing`` / ``conflict`` / ``unsupported``) and does *not* convert.  The
evaluator treats an unverified unit as a gate failure rather than guessing.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

#: Stage -> the numeric filename prefixes ORFS uses for that stage's JSON.
STAGE_FILES: dict[str, tuple[str, ...]] = {
    "synth": ("1_",),
    "floorplan": ("2_",),
    "place": ("3_",),
    "cts": ("4_",),
    "route": ("5_",),
    "finish": ("6_",),
}

#: Leading key segments that name a stage namespace and carry no meaning after
#: the file has already told us which stage this is.
KEY_NAMESPACES: tuple[str, ...] = (
    "synth", "floorplan", "globalplace", "detailedplace", "placeopt", "place",
    "cts", "globalroute", "detailedroute", "route", "finish", "grt", "drt",
)

#: Canonical metric -> candidate keys (after the namespace is stripped), in
#: priority order.  A candidate list rather than a single key is what makes this
#: survive an upstream rename.
METRIC_SPECS: dict[str, list[tuple[str, list[str]]]] = {
    "synth": [
        ("instance_count", ["design__instance__count"]),
        ("instance_area_um2", ["design__instance__area"]),
        ("io_count", ["design__io"]),
        ("net_count", ["design__nets"]),
        ("sequential_count", ["design__instance__count__sequential",
                              "design__instance__count__flop"]),
        ("setup_wns_ns", ["timing__setup__ws"]),
    ],
    "floorplan": [
        ("die_area_um2", ["design__die__area"]),
        ("core_area_um2", ["design__core__area"]),
        ("instance_count", ["design__instance__count"]),
        ("instance_area_um2", ["design__instance__area__stdcell",
                               "design__instance__area"]),
        ("utilization_pct", ["design__instance__utilization",
                             "design__core__util"]),
        ("macro_count", ["design__instance__count__macros"]),
    ],
    "place": [
        ("instance_count", ["design__instance__count"]),
        ("instance_area_um2", ["design__instance__area"]),
        ("utilization_pct", ["design__instance__utilization",
                             "design__core__util"]),
        ("estimated_wirelength_um", ["route__wirelength__estimated",
                                     "design__wirelength__estimated"]),
        ("setup_wns_ns", ["timing__setup__ws"]),
        ("setup_tns_ns", ["timing__setup__tns"]),
        ("displacement_mean_um", ["design__instance__displacement__mean"]),
        ("power_W", ["power__total"]),
    ],
    "cts": [
        ("skew_ns", ["clock__skew__worst", "clock__skew"]),
        ("insertion_delay_ns", ["clock__latency__worst",
                                "clock__insertion__worst",
                                "clock__latency__max"]),
        ("clock_buffer_count", ["design__instance__count__setup_buffer",
                                "design__instance__count__hold_buffer",
                                "design__instance__count__clock_buffer"]),
        ("setup_slack_ns", ["timing__setup__ws"]),
        ("hold_slack_ns", ["timing__hold__ws"]),
        ("setup_tns_ns", ["timing__setup__tns"]),
        ("instance_count", ["design__instance__count"]),
        ("power_W", ["power__total"]),
    ],
    "route": [
        ("wirelength_um", ["route__wirelength"]),
        ("estimated_wirelength_um", ["route__wirelength__estimated"]),
        ("via_count", ["route__vias"]),
        ("via_singlecut_count", ["route__vias__singlecut"]),
        ("via_multicut_count", ["route__vias__multicut"]),
        ("net_count", ["route__net"]),
        ("drc_errors", ["route__drc_errors", "drc__errors"]),
        ("antenna_violations", ["antenna__violating__nets", "antenna_violations"]),
        ("antenna_diode_count", ["antenna_diodes_count"]),
        ("setup_wns_ns", ["timing__setup__ws"]),
        ("setup_tns_ns", ["timing__setup__tns"]),
        ("hold_wns_ns", ["timing__hold__ws"]),
        ("hold_tns_ns", ["timing__hold__tns"]),
        ("congestion_overflow", ["route__congestion__overflow",
                                 "congestion__overflow"]),
        ("grt_overflow_iterations", ["global_route__fastroute__overflow_iterations_s",
                                     "route__overflow__iterations"]),
        ("grt_route_time_s", ["global_route__fastroute__route_l_s"]),
        ("power_W", ["power__total"]),
    ],
    "finish": [
        ("instance_count", ["design__instance__count"]),
        ("instance_area_um2", ["design__instance__area"]),
        ("die_area_um2", ["design__die__area"]),
        ("core_area_um2", ["design__core__area"]),
        ("utilization_pct", ["design__instance__utilization",
                             "design__core__util"]),
        ("setup_wns_ns", ["timing__setup__ws"]),
        ("setup_tns_ns", ["timing__setup__tns"]),
        ("hold_wns_ns", ["timing__hold__ws"]),
        ("drc_errors", ["route__drc_errors", "drc__errors"]),
        ("power_W", ["power__total"]),
        ("warnings", ["flow__warnings__count"]),
        ("errors", ["flow__errors__count"]),
        ("warning_type_count", ["flow__warnings__type_count"]),
    ],
}

#: Metrics reported inconsistently as either a 0-1 ratio or a 0-100 percentage.
PCT_METRICS = frozenset({"utilization_pct"})

#: Every canonical name that carries a time in nanoseconds.
TIME_METRICS = frozenset(
    name for specs in METRIC_SPECS.values() for name, _ in specs
    if name.endswith("_ns")
)

TIME_UNIT_TO_NS = {
    "fs": 1e-6, "ps": 1e-3, "ns": 1.0, "us": 1e3, "ms": 1e6, "s": 1e9,
}

#: The ORFS key carrying the authoritative time unit for the run.
TIME_UNIT_KEY = "run__flow__platform__time_units"

#: Nine decimals keep the precision a ps->ns conversion creates (four decimal
#: places of ps become seven meaningful decimals of ns) while removing binary
#: floating-point noise.
ROUND_DECIMALS = 9


def _time_unit_evidence(raw: dict[str, dict]) -> dict[str, Any]:
    """Read the run's time unit, or say precisely why it could not be read.

    Returning ``scale_to_ns=None`` for anything other than ``verified`` is the
    point: the caller must not convert on a guess.
    """
    observed: list[str] = []
    for payload in raw.values():
        value = payload.get(TIME_UNIT_KEY)
        if isinstance(value, str) and value.strip() and value.strip() not in observed:
            observed.append(value.strip())
    if not observed:
        return {"status": "missing", "raw_values": [], "canonical_unit": "ns",
                "scale_to_ns": None}
    if len(observed) != 1:
        return {"status": "conflict", "raw_values": observed,
                "canonical_unit": "ns", "scale_to_ns": None}
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(fs|ps|ns|us|ms|s)\s*",
                         observed[0], flags=re.I)
    if not match:
        return {"status": "unsupported", "raw_values": observed,
                "canonical_unit": "ns", "scale_to_ns": None}
    scale = float(match.group(1)) * TIME_UNIT_TO_NS[match.group(2).lower()]
    if not math.isfinite(scale) or scale <= 0:
        return {"status": "unsupported", "raw_values": observed,
                "canonical_unit": "ns", "scale_to_ns": None}
    return {"status": "verified", "raw_values": observed,
            "canonical_unit": "ns", "scale_to_ns": scale}


def _load_stage_raw(base: Path) -> dict[str, dict]:
    """Bucket every ``*.json`` by numeric prefix, merging files per stage.

    A stage that emits several JSON files is the normal case, not an exception.
    """
    raw: dict[str, dict] = {stage: {} for stage in STAGE_FILES}
    if not base.is_dir():
        return raw
    for path in sorted(base.glob("*.json")):
        stage = next(
            (name for name, prefixes in STAGE_FILES.items()
             if any(path.name.startswith(p) for p in prefixes)),
            None,
        )
        if stage is None:
            continue
        try:
            payload = json.loads(path.read_text(errors="replace"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict):
            raw[stage].update(payload)
    return raw


def _strip_namespace(key: str) -> str:
    head = key.split("__", 1)
    if len(head) == 2 and head[0] in KEY_NAMESPACES:
        return head[1]
    return key


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _pick(raw: dict, candidates: list[str]) -> float | int | None:
    """Exact match first, then suffix match, so a rename degrades gracefully."""
    stripped = {_strip_namespace(k): v for k, v in raw.items()}
    for candidate in candidates:
        if candidate in stripped:
            number = _number(stripped[candidate])
            if number is not None:
                return number
    for candidate in candidates:
        for key, value in stripped.items():
            if key.endswith(candidate):
                number = _number(value)
                if number is not None:
                    return number
    return None


def extract_metrics_from_log_dir(
    log_dir: str | Path, *, design: str, platform: str,
    clock_period_ns: float | None = None, expected_stage: str = "finish",
) -> dict[str, Any]:
    """Normalize one ORFS leaf log directory.

    Takes the leaf directory rather than a workspace root on purpose: the native
    platform stores evidence under ``logs/<platform>/<design>/base`` while an
    upstream tuner stores each trial in its own leaf directory.  Both are ORFS
    JSON from the same pinned flow, so parsing them must be identical --
    otherwise a baseline gets scored by a different rule than a candidate.
    """
    base = Path(log_dir).expanduser().resolve()
    raw = _load_stage_raw(base)
    time_unit = _time_unit_evidence(raw)

    stages: dict[str, dict[str, Any]] = {}
    for stage, specs in METRIC_SPECS.items():
        source = raw.get(stage) or {}
        if not source:
            stages[stage] = {"status": "not_run", "metrics": {}}
            continue
        metrics: dict[str, Any] = {}
        for name, candidates in specs:
            value = _pick(source, candidates)
            if value is None:
                continue
            if name in TIME_METRICS and time_unit["status"] == "verified":
                value = value * time_unit["scale_to_ns"]
            if name in PCT_METRICS and value <= 1.0:
                value = value * 100.0
            if isinstance(value, float):
                value = round(value, ROUND_DECIMALS)
            metrics[name] = value
        slack = metrics.get("setup_wns_ns", metrics.get("setup_slack_ns"))
        if clock_period_ns and slack is not None and clock_period_ns - slack > 0:
            metrics["fmax_mhz"] = round(1000.0 / (clock_period_ns - slack), 2)
        stages[stage] = {"status": "completed", "metrics": metrics}

    order = list(STAGE_FILES)
    if expected_stage not in order:
        raise ValueError(f"unsupported expected stage: {expected_stage!r}")
    expected = order[:order.index(expected_stage) + 1]
    done = [name for name in expected if stages[name]["status"] == "completed"]

    # Merge per metric across the two stages that own the terminal numbers.
    # Detailed route owns the terminal DRC count while the final report owns
    # timing, power and area.  Replacing one dictionary with the other loses an
    # explicit route DRC of 0 and turns a clean run into missing data.
    terminal = {**stages["route"]["metrics"], **stages["finish"]["metrics"]}
    setup = terminal.get("setup_wns_ns")
    hold = terminal.get("hold_wns_ns")
    drc = terminal.get("drc_errors")
    antenna = stages["route"]["metrics"].get("antenna_violations")
    timing_bad = ((setup is not None and setup < 0)
                  or (hold is not None and hold < 0))
    physical_bad = bool(drc) or bool(antenna)

    if len(done) < len(expected):
        overall = "incomplete"
    elif timing_bad or physical_bad:
        overall = "violations"
    elif expected_stage == "finish":
        overall = "clean"
    else:
        overall = "target_stage_complete"

    return {
        "design": design,
        "platform": platform,
        "log_dir": str(base),
        "clock_period_ns": clock_period_ns,
        "units": {
            "time": time_unit,
            "area": {"canonical_unit": "um^2"},
            "power": {"canonical_unit": "W", "source": "ORFS metric contract"},
        },
        "stages": stages,
        "summary": {
            "stages_completed": len(done),
            "stages_total": len(expected),
            "expected_stage": expected_stage,
            "signoff_complete": (expected_stage == "finish"
                                 and len(done) == len(expected)),
            "has_timing_violation": timing_bad,
            "has_drc_errors": physical_bad,
            "overall_status": overall,
        },
    }
