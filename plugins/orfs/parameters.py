"""Typed, allowlisted ORFS tuning parameters.

Ported from the frozen v1 implementation.  The table, the bounds and the
cross-parameter rules are recorded behaviour: each bound was chosen against a
real run, and each rule exists because its absence produced a wrong result.

The design rule at the top of the table is the important one:

    **Clock and SDC constraints are deliberately absent.**  A QoR optimizer must
    not be able to improve its score by weakening the design target.  If the
    clock period were a searchable parameter, "better timing" would sometimes
    mean "an easier design", and no amount of statistics downstream could
    recover the meaning of the comparison.

Two other things here that are easy to get wrong:

* ``place_density`` and ``place_density_lb_addon`` are **alternative policies**,
  not two knobs.  ORFS resolves them differently, so setting both leaves an
  inactive contradictory value in the evidence.
* The SKY130HD utilization lower bound is 20, not 30, because the reviewed
  upstream anchor configuration uses 20.  A platform gate that rejected it would
  refuse the published starting point of the very study being reproduced.

``runtime_patterns`` are not decoration.  They are the regexes a log line must
match to prove the parameter actually reached the tool, which is what separates
"we set it" from "the tool used it".
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from digest import sha256_file

#: Constraint values that are deliberately not tunable.  Named so a reader can
#: see the exclusion is intentional rather than an omission.
FROZEN_CONSTRAINTS = ("clock_period_ns", "clock_uncertainty", "io_delay")


@dataclass(frozen=True)
class ORFSParameter:
    name: str
    env_name: str
    kind: str
    stage: str
    lower: float | None = None
    upper: float | None = None
    step: float | None = None
    choices: tuple[Any, ...] = ()
    platforms: tuple[str, ...] = ()
    consumer_path: str = "scripts/variables.yaml"
    runtime_patterns: tuple[str, ...] = field(default=())

    def canonicalize(self, raw: Any) -> Any:
        """Coerce and validate one value, or refuse it.

        ``bool`` is rejected where a number is expected on purpose: Python
        treats ``True`` as ``1``, so accepting it would let a typo silently
        become a valid parameter value.
        """
        if self.kind == "bool":
            if raw is True or raw == 1:
                value: Any = 1
            elif raw is False or raw == 0:
                value = 0
            else:
                raise ValueError(f"{self.name} must be boolean")
        elif self.kind == "int":
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise ValueError(f"{self.name} must be an integer")
            value = int(raw)
            if float(raw) != value:
                raise ValueError(f"{self.name} must be an integer")
        elif self.kind == "float":
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise ValueError(f"{self.name} must be numeric")
            value = float(raw)
            if not math.isfinite(value):
                raise ValueError(f"{self.name} must be finite")
        elif self.kind == "categorical":
            value = raw
        else:
            raise ValueError(f"unsupported parameter kind: {self.kind}")

        if self.choices and value not in self.choices:
            raise ValueError(f"{self.name} is not in its allowlisted choices")
        if self.lower is not None and value < self.lower:
            raise ValueError(f"{self.name} is below its lower bound")
        if self.upper is not None and value > self.upper:
            raise ValueError(f"{self.name} is above its upper bound")
        if self.step and self.lower is not None:
            units = (float(value) - self.lower) / self.step
            if abs(units - round(units)) > 1e-8:
                raise ValueError(
                    f"{self.name} does not align with its quantization step"
                )
        return value


ORFS_PARAMETERS: tuple[ORFSParameter, ...] = (
    ORFSParameter("core_utilization_pct", "CORE_UTILIZATION", "int", "floorplan",
                  20, 80, 1,
                  runtime_patterns=(
                      r"Defining die area using utilization:\s*{value}(?:\.0+)?%",)),
    ORFSParameter("place_density", "PLACE_DENSITY", "float", "place", .30, .90, .01,
                  runtime_patterns=(r"Placement target density:\s*{value}",)),
    # Modelled upstream as a continuous real dimension.  OpenROAD consumes the
    # value as a Tcl numeric literal, so imposing a platform grid here would
    # silently turn several distinct proposals into the same run.
    ORFSParameter("place_density_lb_addon", "PLACE_DENSITY_LB_ADDON", "float",
                  "place", 0, .50,
                  runtime_patterns=(
                      r"computed from PLACE_DENSITY_LB_ADDON\s+{value}",)),
    ORFSParameter("tns_end_percent", "TNS_END_PERCENT", "int", "place", 0, 100, 1,
                  consumer_path="scripts/repair_timing_post_place.tcl",
                  runtime_patterns=(
                      r"repair_timing[^\n]*-repair_tns\s+{value}",)),
    ORFSParameter("global_placement_padding",
                  "CELL_PAD_IN_SITES_GLOBAL_PLACEMENT", "int", "place", 0, 4, 1,
                  consumer_path="scripts/global_place.tcl",
                  runtime_patterns=(
                      r"global_placement[^\n]*-pad_left\s+{value}\s+-pad_right\s+{value}",)),
    ORFSParameter("detail_placement_padding",
                  "CELL_PAD_IN_SITES_DETAIL_PLACEMENT", "int", "place", 0, 4, 1,
                  consumer_path="scripts/detail_place.tcl"),
    ORFSParameter("enable_dpo", "ENABLE_DPO", "bool", "place", choices=(0, 1),
                  consumer_path="scripts/detail_place.tcl",
                  runtime_patterns=(r"Detailed placement improvement\.",)),
    ORFSParameter("gpl_timing_driven", "GPL_TIMING_DRIVEN", "bool", "place",
                  choices=(0, 1), consumer_path="scripts/global_place.tcl",
                  runtime_patterns=(r"global_placement[^\n]*-timing_driven",)),
    ORFSParameter("gpl_routability_driven", "GPL_ROUTABILITY_DRIVEN", "bool",
                  "place", choices=(0, 1),
                  consumer_path="scripts/global_place.tcl",
                  runtime_patterns=(r"global_placement[^\n]*-routability_driven",)),
    ORFSParameter("routing_layer_adjustment", "ROUTING_LAYER_ADJUSTMENT", "float",
                  "floorplan", .10, .90, .01,
                  consumer_path="scripts/floorplan.tcl"),
    ORFSParameter("cts_cluster_size", "CTS_CLUSTER_SIZE", "int", "cts", 10, 40, 1,
                  consumer_path="scripts/cts.tcl",
                  runtime_patterns=(
                      r"clock_tree_synthesis[^\n]*-sink_clustering_size\s+{value}",)),
    ORFSParameter("cts_cluster_diameter", "CTS_CLUSTER_DIAMETER", "float", "cts",
                  40, 120, 1, consumer_path="scripts/cts.tcl",
                  runtime_patterns=(
                      r"clock_tree_synthesis[^\n]*-sink_clustering_max_diameter\s+{value}(?:\.0+)?",)),
)

ORFS_PARAMETER_BY_NAME = {item.name: item for item in ORFS_PARAMETERS}

#: Per-platform calibrated bounds.  Narrower than the global ones because a
#: value that is legal everywhere may still be wrong for a specific PDK.
ORFS_PLATFORM_BOUNDS: dict[str, dict[str, tuple[float, float]]] = {
    "nangate45": {"core_utilization_pct": (20, 80), "place_density": (.30, .80),
                  "global_placement_padding": (0, 3),
                  "detail_placement_padding": (0, 3)},
    "asap7": {"core_utilization_pct": (30, 75), "place_density": (.35, .80),
              "global_placement_padding": (0, 3),
              "detail_placement_padding": (0, 3)},
    # The published SKY130HD anchor uses utilization 20.  Preserving the
    # reviewed upstream lower bound is what stops this gate from rejecting the
    # official starting configuration of the study being reproduced.
    "sky130hd": {"core_utilization_pct": (20, 70), "place_density": (.30, .75),
                 "global_placement_padding": (0, 3),
                 "detail_placement_padding": (0, 3)},
}


def validate_orfs_parameters(parameters: Mapping[str, Any], *,
                             platform: str) -> dict[str, Any]:
    """Canonicalize and gate a tuning request.

    An unknown name is refused rather than ignored: a caller who mistypes a
    parameter should be told, not silently given a run that ignores it.
    """
    unknown = sorted(set(parameters) - set(ORFS_PARAMETER_BY_NAME))
    if unknown:
        raise ValueError(f"unsupported ORFS tuning parameters: {', '.join(unknown)}")

    result: dict[str, Any] = {}
    for name, raw in parameters.items():
        spec = ORFS_PARAMETER_BY_NAME[name]
        if spec.platforms and platform not in spec.platforms:
            raise ValueError(f"{name} is not supported on {platform}")
        result[name] = spec.canonicalize(raw)
        bound = ORFS_PLATFORM_BOUNDS.get(platform, {}).get(name)
        if bound and not bound[0] <= result[name] <= bound[1]:
            raise ValueError(
                f"{name} is outside the calibrated range for {platform}"
            )

    global_pad = result.get("global_placement_padding")
    detail_pad = result.get("detail_placement_padding")
    if global_pad is not None and detail_pad is not None and detail_pad > global_pad:
        raise ValueError(
            "detail_placement_padding cannot exceed global_placement_padding"
        )
    if "place_density" in result and "place_density_lb_addon" in result:
        raise ValueError(
            "place_density and place_density_lb_addon are alternative policies"
        )
    return dict(sorted(result.items()))


def effective_configuration_id(parameters: Mapping[str, Any], *,
                               platform: str) -> str:
    """A stable identity for one effective configuration.

    Two runs with the same id were configured identically; the id is what makes
    that checkable without diffing the whole evidence tree.
    """
    canonical = validate_orfs_parameters(parameters, platform=platform)
    payload = {"schema_version": 1, "platform": platform,
               "parameters": canonical}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"orfs-effective-{digest}"


def orfs_parameter_config_lines(parameters: Mapping[str, Any], *,
                                platform: str) -> list[str]:
    canonical = validate_orfs_parameters(parameters, platform=platform)
    return [
        f"export {ORFS_PARAMETER_BY_NAME[name].env_name} = {value}"
        for name, value in canonical.items()
    ]


def orfs_parameter_schema() -> dict[str, Any]:
    return {
        "schema_version": 3,
        "liveness_rule_version": "orfs-runtime-patterns-v2",
        "frozen_constraints": list(FROZEN_CONSTRAINTS),
        "parameters": [item.__dict__ for item in ORFS_PARAMETERS],
        "platform_bounds": ORFS_PLATFORM_BOUNDS,
    }


def parameter_source_evidence(flow_home: str | Path,
                              parameters: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Static evidence that each parameter has something that consumes it.

    For every parameter this records whether the ORFS script that supposedly
    reads it exists, and whether that script actually mentions the variable.
    A parameter whose consumer does not mention it is set and ignored, which is
    the failure this catches without running the flow.
    """
    root = Path(flow_home).expanduser().resolve()
    rows = []
    for name in sorted(parameters):
        spec = ORFS_PARAMETER_BY_NAME[name]
        path = root / spec.consumer_path
        content = path.read_bytes() if path.is_file() else b""
        rows.append({
            "name": name,
            "env_name": spec.env_name,
            "consumer_path": str(path),
            "consumer_sha256": sha256_file(path) if content else None,
            "consumer_declared": spec.env_name.encode() in content,
            "stage": spec.stage,
            "runtime_patterns": list(spec.runtime_patterns),
        })
    return rows
