"""Explicit, reproducible QoR preference profiles for candidate selection."""
from __future__ import annotations
from typing import Any

METRICS = {"area": ("finish__design__instance__area", "min"),
           "timing": ("finish__timing__setup__ws", "max"),
           "power": ("finish__power__total", "min")}

def objective_profile(name: str) -> tuple[dict[str, Any], ...]:
    if name in METRICS: return (_item(name, 1.0),)
    if name == "balanced": return (_item("timing", .40), _item("area", .35), _item("power", .25))
    raise ValueError("unknown objective profile")


def profile_hard_constraints(name: str) -> tuple[dict[str, Any], ...]:
    """Non-negotiable implementation acceptance gates for every QoR profile.

    A lower area/power number is never a win when the candidate has negative
    setup slack or a routing DRC failure.  Keeping this separate from weights
    makes the web preference a real *choice among valid implementations*, not
    a license to trade functional implementation validity for a score.
    ``name`` is checked here as well so callers cannot accidentally apply the
    gates to an unrecognised preference label.
    """
    objective_profile(name)
    return (
        {"metric": "finish__timing__setup__ws", "operator": ">=", "threshold": 0.0},
        {"metric": "has_drc_errors", "operator": "==", "threshold": False},
    )

def profile_grid(parameters: dict[str, Any]) -> dict[str, list[float]]:
    """Bounded 2^3 factorial seed so interaction evidence can be collected."""
    util=float(parameters.get("core_utilization_pct",30)); density=float(parameters.get("place_density",.55)); period=float(parameters.get("clock_period_ns",10))
    return {"core_utilization_pct":[max(1.,util-5),min(99.,util+5)],"place_density":[max(.01,density-.05),min(1.,density+.05)],"clock_period_ns":[max(.01,period*.9),period*1.1]}

def _item(key: str, weight: float) -> dict[str, Any]:
    metric,direction=METRICS[key]; return {"metric":metric,"direction":direction,"weight":weight}
