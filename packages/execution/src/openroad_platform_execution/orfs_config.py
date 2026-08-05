from __future__ import annotations

import re
import shutil
from pathlib import Path


CLOCK_CANDIDATES = ("clk", "clock", "i_clk", "clk_i", "sys_clk", "clk_in")

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


def strip_comments(rtl: str) -> str:
    rtl = re.sub(r"/\*.*?\*/", " ", rtl, flags=re.S)
    return re.sub(r"//[^\n]*", " ", rtl)


def infer_top(rtl: str, fallback: str) -> str:
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
    minimum_die_size_um: float | None = None,
) -> Path:
    config_dir = workdir / "designs" / platform / design
    source_dir = workdir / "designs" / "src" / design
    config_dir.mkdir(parents=True, exist_ok=True)
    source_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(rtl_path, source_dir / f"{design}.v")

    lines = [
        f"export DESIGN_NAME = {design}",
        f"export PLATFORM = {platform}",
        "export VERILOG_FILES = $(DESIGN_HOME)/src/$(DESIGN_NAME)/$(DESIGN_NAME).v",
        f"export SDC_FILE = $(DESIGN_HOME)/{platform}/$(DESIGN_NAME)/constraint.sdc",
        f"export CLOCK_PERIOD = {clock_period_ns:g}",
        f"export PLACE_DENSITY = {place_density:g}",
    ]
    if platform == "nangate45":
        (config_dir / "pdn.tcl").write_text(PDN_SIMPLE, encoding="utf-8")
        lines.append(f"export PDN_TCL = $(DESIGN_HOME)/{platform}/$(DESIGN_NAME)/pdn.tcl")
        if minimum_die_size_um is not None:
            size = float(minimum_die_size_um)
            margin = max(1.0, min(10.0, size * 0.1))
            lines.extend((
                f"export DIE_AREA = 0 0 {size:g} {size:g}",
                f"export CORE_AREA = {margin:g} {margin:g} {size-margin:g} {size-margin:g}",
            ))
        else:
            lines.append(f"export CORE_UTILIZATION = {core_utilization_pct:g}")
    else:
        minimum_die = {"sky130hd": 60, "asap7": 30, "gf180": 80}.get(platform, 50)
        lines.append(f"export DIE_AREA = 0 0 {minimum_die} {minimum_die}")

    config_path = config_dir / "config.mk"
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if clock:
        sdc = (
            f"create_clock -name {clock} -period {clock_period_ns:g} [get_ports {{{clock}}}]\n"
            f"set_input_delay -clock {clock} [expr {clock_period_ns:g} * 0.2] [all_inputs]\n"
            f"set_output_delay -clock {clock} [expr {clock_period_ns:g} * 0.2] [all_outputs]\n"
        )
    else:
        sdc = (
            f"create_clock -name vclk -period {clock_period_ns:g}\n"
            f"set_input_delay -clock vclk [expr {clock_period_ns:g} * 0.2] [all_inputs]\n"
            f"set_output_delay -clock vclk [expr {clock_period_ns:g} * 0.2] [all_outputs]\n"
        )
    (config_dir / "constraint.sdc").write_text(sdc, encoding="utf-8")
    return config_path
