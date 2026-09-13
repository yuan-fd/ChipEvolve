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

import hashlib
import json
import re
import shutil
from pathlib import Path

from design_options import design_option_config_lines
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

#: Header suffixes copied out of an include directory.  Copying the directory
#: wholesale would carry build products and vendor blobs into the attempt.
INCLUDE_SUFFIXES = frozenset({".v", ".sv", ".vh", ".svh"})

#: A design or platform name becomes a directory and a Make value.
SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,95}$")

#: A clock or frontend name is interpolated into Tcl; it is a Verilog identifier.
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,95}$")


def generated_sdc(clock: str | None, period: float) -> str:
    """The constraint file for a design whose SDC we have to write ourselves.

    A design with a real clock port gets a clock on that port.  A design with no
    identifiable clock gets a *virtual* clock on purpose: naming a port that does
    not exist makes ``get_ports`` return nothing, so the constraint would apply
    to no path at all while looking like it constrained something.
    """
    name = clock or "vclk"
    create = f"create_clock -name {name} -period {period:g}"
    if clock is not None:
        create += f" [get_ports {{{clock}}}]"
    return (
        create + "\n"
        f"set_input_delay -clock {name} [expr {period:g} * {IO_DELAY_FRACTION}] "
        "[all_inputs]\n"
        f"set_output_delay -clock {name} [expr {period:g} * {IO_DELAY_FRACTION}] "
        "[all_outputs]\n"
    )


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


def _stage_rtl(
    *, source_dir: Path, rtl_files: tuple[Path, ...], rtl_root: Path | None,
    rtl_include_dirs: tuple[Path, ...],
) -> tuple[list[Path], list[Path]]:
    """Copy the design's sources into the attempt workspace.

    There is one shape here -- a rooted bundle -- because a single file is that
    bundle with one entry.  The task protocol's "one file or many" convenience
    belongs to the adapter, which normalizes it; a writer that accepted both
    would need a precedence rule for a caller who gave both, and the caller
    should be told which one took effect instead of having to discover it.

    Returns the staged sources and include directories, all inside ``source_dir``.
    """
    sources = tuple(Path(item).expanduser().resolve() for item in rtl_files)
    if not sources:
        raise ValueError("a design needs at least one RTL source")
    if rtl_root is None:
        raise ValueError("rtl_root is required for an RTL bundle")
    root = Path(rtl_root).expanduser().resolve()

    source_dir.mkdir(parents=True, exist_ok=True)
    staged_sources: list[Path] = []
    for source in sources:
        try:
            relative = source.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"RTL source {source} is not under rtl_root {root}") from exc
        target = source_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        staged_sources.append(target)

    staged_dirs: list[Path] = []
    for directory in rtl_include_dirs:
        resolved_dir = Path(directory).expanduser().resolve()
        try:
            relative_dir = resolved_dir.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"include directory {resolved_dir} is not under rtl_root {root}"
            ) from exc
        target_dir = source_dir / relative_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        for header in sorted(resolved_dir.rglob("*")):
            if header.is_file() and header.suffix.lower() in INCLUDE_SUFFIXES:
                target = target_dir / header.relative_to(resolved_dir)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(header, target)
        staged_dirs.append(target_dir)

    return staged_sources, staged_dirs


def _file_record(path: Path, *, relative_to: Path) -> dict:
    """Identify one staged file.

    The hash is of the *staged* bytes, which is what the flow will read.  Hashing
    the operator's original would describe a file the run never opened.
    """
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": str(path.relative_to(relative_to)),
        "size_bytes": path.stat().st_size,
        "sha256": digest.hexdigest(),
    }


def _identifier(value: str, label: str) -> str:
    """A name that is safe to interpolate into the generated Tcl.

    The SDC is Tcl and the configuration is Make; a value carrying whitespace or
    a newline would not be a bad *name*, it would be a second statement.  This is
    boundary validation, so it fails loudly and names the field.
    """
    if not IDENTIFIER.match(value):
        raise ValueError(f"{label} is not a valid identifier: {value!r}")
    return value


def _name(value: str, label: str) -> str:
    """A name that becomes a directory and a Make value."""
    if not SAFE_NAME.match(value):
        raise ValueError(f"{label} is not a usable name: {value!r}")
    return value


def write_design_files(
    *,
    workdir: Path,
    design: str,
    platform: str,
    clock: str | None,
    clock_period_ns: float,
    core_utilization_pct: float,
    place_density: float,
    rtl_files: tuple[Path, ...] = (),
    rtl_root: Path | None = None,
    rtl_include_dirs: tuple[Path, ...] = (),
    synth_hdl_frontend: str | None = None,
    sdc_path: Path | None = None,
    fast_route_tcl_path: Path | None = None,
    or_seed: int = 1,
    minimum_die_size_um: float | None = None,
    flow_parameters: dict | None = None,
    design_options: dict | None = None,
) -> Path:
    """Materialize the design under the attempt workspace and return config.mk.

    Sources are copied into the workspace rather than referenced in place: the
    operator's ORFS tree is shared and must never be written to by a run.

    Everything a reference design needs is here -- a multi-file bundle with its
    include directories, a synthesis frontend, a reviewable SDC, a fast-route
    script and the design's recipe options -- because a bundle that carried those
    and could not spend them would record an attempt that elaborated a different
    design than the one requested.
    """
    # DESIGN_NAME is also the top module name in ORFS, so it must be a Verilog
    # identifier and not merely a safe directory name.
    design = _identifier(design, "design")
    platform = _name(platform, "platform")
    if clock is not None:
        clock = _identifier(clock, "clock")
    if synth_hdl_frontend is not None:
        synth_hdl_frontend = _identifier(synth_hdl_frontend, "synth_hdl_frontend")
    # Validate the options before anything is written: a refused request must not
    # leave a half-materialized design behind.
    option_lines = design_option_config_lines(design_options)

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
    config_dir.mkdir(parents=True, exist_ok=True)
    sources, include_dirs = _stage_rtl(
        source_dir=workdir / "designs" / "src" / design, rtl_files=rtl_files,
        rtl_root=rtl_root, rtl_include_dirs=rtl_include_dirs,
    )

    lines = [
        f"export DESIGN_NAME = {design}",
        f"export PLATFORM = {platform}",
        "export VERILOG_FILES = " + " ".join(str(item) for item in sources),
        f"export SDC_FILE = $(DESIGN_HOME)/{platform}/$(DESIGN_NAME)/constraint.sdc",
        f"export CLOCK_PERIOD = {clock_period_native:g}",
        f"export OR_SEED = {or_seed}",
    ]
    # ORFS treats PLACE_DENSITY and PLACE_DENSITY_LB_ADDON as alternative
    # policies.  Emitting both is deterministic in effect but leaves an inactive
    # contradictory value in the evidence, so only one is written.
    if "place_density_lb_addon" not in tuning:
        lines.append(f"export PLACE_DENSITY = {place_density:g}")

    if include_dirs:
        lines.append("export VERILOG_INCLUDE_DIRS = " + " ".join(
            str(item) for item in include_dirs))
    if synth_hdl_frontend:
        lines.append(f"export SYNTH_HDL_FRONTEND = {synth_hdl_frontend}")
    lines.extend(option_lines)

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

    if fast_route_tcl_path is not None:
        source_fast_route = Path(fast_route_tcl_path).expanduser().resolve()
        if not source_fast_route.is_file() or source_fast_route.stat().st_size == 0:
            raise FileNotFoundError(source_fast_route)
        shutil.copyfile(source_fast_route, config_dir / "fastroute.tcl")
        lines.append(
            "export FASTROUTE_TCL = "
            f"$(DESIGN_HOME)/{platform}/$(DESIGN_NAME)/fastroute.tcl"
        )

    # Everything else the caller tuned, through the real allowlist so the
    # names and value shapes come from the parameter table rather than from
    # upper-casing a key.
    extra = {name: value for name, value in tuning.items()
             if name not in {"core_utilization_pct", "place_density"}}
    lines.extend(orfs_parameter_config_lines(extra, platform=platform))

    # What was materialized, recorded as it was materialized.  The evaluator
    # hashes this for the design's identity when a task carries no bundle
    # fingerprint, and it is the only place the *staged* bytes of every source
    # are written down together: a design whose sources changed between two runs
    # must not compare as the same design.
    staged_root = workdir / "designs" / "src" / design
    (workdir / "design_input_manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "kind": "ordered-rtl-bundle",
        "top": design,
        "platform": platform,
        "source_order": [str(item.relative_to(staged_root)) for item in sources],
        "sources": [_file_record(item, relative_to=staged_root) for item in sources],
        "include_dirs": [
            {"path": str(item.relative_to(staged_root)),
             "headers": [
                 _file_record(header, relative_to=staged_root)
                 for header in sorted(item.rglob("*"))
                 if header.is_file()
                 and header.suffix.lower() in INCLUDE_SUFFIXES
             ]}
            for item in include_dirs
        ],
        "synth_hdl_frontend": synth_hdl_frontend,
        "design_options": dict(design_options or {}),
    }, indent=2, sort_keys=True), encoding="utf-8")

    config_path = config_dir / "config.mk"
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # A supplied SDC is the design's own constraint file and is copied verbatim:
    # the reviewed ASAP7 ibex recipe depends on the exact bytes of its
    # positive-slack constraint, so regenerating one here would silently change
    # the design under test.
    constraint_path = config_dir / "constraint.sdc"
    if sdc_path is not None:
        resolved_sdc = Path(sdc_path).expanduser().resolve()
        if not resolved_sdc.is_file() or resolved_sdc.stat().st_size == 0:
            raise FileNotFoundError(resolved_sdc)
        shutil.copyfile(resolved_sdc, constraint_path)
    else:
        constraint_path.write_text(
            generated_sdc(clock, clock_period_native), encoding="utf-8")
    return config_path
