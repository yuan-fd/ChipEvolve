"""Turn a design + request into the files ORFS needs.

Ported from the frozen v1 implementation.  Every branching rule below is a
recorded failure, which is why it is carried across rather than re-derived.

The floorplan rule is the clearest example.  ORFS needs exactly one complete
floorplan-initialisation policy.  A ``DIE_AREA`` on its own deliberately
disables its automatic utilisation-based sizing, but it still leaves no
``CORE_AREA`` for ``initialize_floorplan`` -- so emitting ``DIE_AREA`` alone made
every generated sky130/asap7/gf180 task stop at floorplan with "No floorplan
initialization method specified".  The rule is therefore: use
``CORE_UTILIZATION`` unless a minimum die size was explicitly requested, in
which case emit **both** rectangles.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from parameters import orfs_parameter_config_lines, validate_orfs_parameters

#: Port names tried in order when the RTL does not make the clock obvious.
CLOCK_CANDIDATES = ("clk", "clock", "i_clk", "clk_i", "sys_clk", "clk_in")

#: Public API and task periods are always nanoseconds.  SDC numeric literals are
#: expressed in the active platform's Liberty unit, which is **picoseconds for
#: ASAP7** and nanoseconds elsewhere.  Confining this conversion to the
#: execution boundary is what stops a PDK-dependent unit leaking into stored
#: request data -- and it is the exact counterpart of the parser's ps->ns
#: conversion on the way back out.
PLATFORM_TIME_UNIT_NS: dict[str, float] = {"asap7": 1e-3}

#: Power distribution network for platforms that do not ship their own.  The
#: layer names and pitches are nangate45-specific; using this elsewhere would
#: silently produce a wrong PDN rather than an error, so it is applied only for
#: the platform it was written for.
PDN_SIMPLE = """\
add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {^VDD$} -power
add_global_connection -net {VSS} -inst_pattern {.*} -pin_pattern {^VSS$} -ground
global_connect
set_voltage_domain -name {CORE} -power {VDD} -ground {VSS}
define_pdn_grid -name {grid} -voltage_domains {CORE} -pins {metal7}
add_pdn_stripe -grid {grid} -layer {metal1} -width {0.17} -pitch {2.4} -offset {0} -followpins
add_pdn_stripe -grid {grid} -layer {metal4} -width {0.48} -pitch {6.0} -offset {0.3}
add_pdn_stripe -grid {grid} -layer {metal7} -width {0.40} -pitch {3.0} -offset {0.1}
add_pdn_connect -grid {grid} -layers {metal1 metal4}
add_pdn_connect -grid {grid} -layers {metal4 metal7}
"""

#: Platforms whose PDN template the platform supplies itself.
PLATFORMS_WITH_GENERATED_PDN = frozenset({"nangate45"})

#: Fraction of the period used for input and output delay in the generated SDC.
IO_DELAY_FRACTION = 0.2


def clock_period_in_platform_units(clock_period_ns: float, platform: str) -> float:
    """Convert a nanosecond period into the platform's Liberty unit."""
    return float(clock_period_ns) / PLATFORM_TIME_UNIT_NS.get(platform, 1.0)


def strip_comments(rtl: str) -> str:
    rtl = re.sub(r"/\*.*?\*/", " ", rtl, flags=re.S)
    return re.sub(r"//[^\n]*", " ", rtl)


def infer_top(rtl: str, fallback: str) -> str:
    """Pick the top module: a defined module that nothing instantiates.

    Falls back to the requested name when it exists, then to the last defined
    module, so a multi-module file still has a deterministic answer.
    """
    code = strip_comments(rtl)
    defined = re.findall(r"\bmodule\s+(\w+)", code)
    if not defined:
        raise ValueError("RTL does not contain a module declaration")
    keywords = {"module", "endmodule", "input", "output", "inout", "wire",
                "reg", "assign", "always", "if", "else", "case", "begin", "end"}
    instantiated = {
        item for item in re.findall(
            r"^\s*(\w+)\s*(?:#\s*\([^)]*\)\s*)?\w+\s*\(", code, flags=re.M
        ) if item not in keywords
    }
    candidates = [item for item in defined if item not in instantiated]
    if len(candidates) == 1:
        return candidates[0]
    if fallback in defined:
        return fallback
    return candidates[0] if candidates else defined[-1]


def infer_clock(rtl: str, top: str) -> str | None:
    """Find the clock port by name, then by any clock-looking port."""
    code = strip_comments(rtl)
    match = re.search(rf"\bmodule\s+{re.escape(top)}\b(.*?)\bendmodule\b", code, re.S)
    body = match.group(1) if match else code
    ports = set(re.findall(
        r"\binput\s+(?:wire\s+|reg\s+)?(?:\[[^\]]*\]\s*)?(\w+)", body
    ))
    ports |= set(re.findall(r"\w+", body.split(";", 1)[0]))
    for candidate in CLOCK_CANDIDATES:
        if candidate in ports:
            return candidate
    return next((port for port in ports if re.search(r"cl(?:k|ock)", port, re.I)), None)


def write_design_files(
    *,
    workdir: Path,
    rtl_path: Path,
    design: str,
    platform: str,
    clock: str | None,
    clock_period_ns: float,
    core_utilization_pct: float,
    place_density: float,
    or_seed: int = 1,
    minimum_die_size_um: float | None = None,
    flow_parameters: dict | None = None,
) -> Path:
    """Materialize the design under the attempt workspace and return config.mk.

    Sources are copied into the workspace rather than referenced in place: the
    operator's ORFS tree is shared and must never be written to by a run.
    """
    clock_period_native = clock_period_in_platform_units(clock_period_ns, platform)

    # Validate the *effective* set, not just ``flow_parameters``.  The frozen
    # implementation validated only the tuning dict and then used the explicit
    # arguments unchecked when it was absent, so a caller could pass an
    # out-of-range utilization straight through the gate.  Merging them first
    # closes that without changing the result for any valid input.
    tuning = dict(flow_parameters or {})
    # ``core_utilization_pct`` and ``place_density`` are defaults the caller
    # supplies, not independent requests.  Choosing the addon policy supersedes
    # the density default rather than contradicting it, so the default is only
    # folded in when the caller did not choose the alternative.
    using_addon = "place_density_lb_addon" in tuning
    if not using_addon:
        tuning.setdefault("place_density", place_density)
    tuning.setdefault("core_utilization_pct", core_utilization_pct)
    tuning = validate_orfs_parameters(tuning, platform=platform)

    core_utilization_pct = float(tuning["core_utilization_pct"])
    place_density = float(tuning.get("place_density", place_density))

    config_dir = workdir / "designs" / platform / design
    source_dir = workdir / "designs" / "src" / design
    config_dir.mkdir(parents=True, exist_ok=True)
    source_dir.mkdir(parents=True, exist_ok=True)

    target = source_dir / f"{design}{rtl_path.suffix.lower() or '.v'}"
    shutil.copyfile(rtl_path, target)

    lines = [
        f"export DESIGN_NAME = {design}",
        f"export PLATFORM = {platform}",
        f"export VERILOG_FILES = {target}",
        f"export SDC_FILE = $(DESIGN_HOME)/{platform}/$(DESIGN_NAME)/constraint.sdc",
        f"export CLOCK_PERIOD = {clock_period_native:g}",
        f"export OR_SEED = {or_seed}",
    ]
    # ORFS treats PLACE_DENSITY and PLACE_DENSITY_LB_ADDON as alternative
    # policies.  Emitting both is deterministic in effect but leaves an inactive
    # contradictory value in the evidence, so only one is written.
    if "place_density_lb_addon" not in tuning:
        lines.append(f"export PLACE_DENSITY = {place_density:g}")

    if platform in PLATFORMS_WITH_GENERATED_PDN:
        (config_dir / "pdn.tcl").write_text(PDN_SIMPLE, encoding="utf-8")
        lines.append(
            f"export PDN_TCL = $(DESIGN_HOME)/{platform}/$(DESIGN_NAME)/pdn.tcl"
        )

    if minimum_die_size_um is None:
        lines.append(f"export CORE_UTILIZATION = {core_utilization_pct:g}")
    else:
        # Both rectangles, or ORFS stops at floorplan.
        size = float(minimum_die_size_um)
        margin = max(1.0, min(10.0, size * 0.1))
        lines.extend((
            f"export DIE_AREA = 0 0 {size:g} {size:g}",
            f"export CORE_AREA = {margin:g} {margin:g} "
            f"{size - margin:g} {size - margin:g}",
        ))

    # Everything else the caller tuned, through the real allowlist so the
    # names and value shapes come from the parameter table rather than from
    # upper-casing a key.
    extra = {name: value for name, value in tuning.items()
             if name not in {"core_utilization_pct", "place_density"}}
    lines.extend(orfs_parameter_config_lines(extra, platform=platform))

    config_path = config_dir / "config.mk"
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    constraint_path = config_dir / "constraint.sdc"
    clock_name = clock or "vclk"
    constraint_path.write_text(
        f"create_clock -name {clock_name} -period {clock_period_native:g} "
        f"[get_ports {{{clock_name}}}]\n"
        f"set_input_delay -clock {clock_name} "
        f"[expr {clock_period_native:g} * {IO_DELAY_FRACTION}] [all_inputs]\n"
        f"set_output_delay -clock {clock_name} "
        f"[expr {clock_period_native:g} * {IO_DELAY_FRACTION}] [all_outputs]\n",
        encoding="utf-8",
    )
    return config_path
