"""ORFS execution knowledge: configuration and stage gating.

Each assertion here is a rule that was learned by running the real flow.  Two
are worth calling out because getting them wrong is silent:

* the clock period is written in the platform's Liberty unit, so an ASAP7
  configuration carries a period 1000x larger than the nanosecond value the API
  speaks -- the exact counterpart of the parser's conversion on the way back.
* a bare ``DIE_AREA`` without ``CORE_AREA`` disables ORFS's utilisation-based
  sizing and still leaves ``initialize_floorplan`` with no area, so the flow
  stops at floorplan.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config import (
    CLOCK_CANDIDATES,
    PLATFORM_TIME_UNIT_NS,
    clock_period_in_platform_units,
    infer_clock,
    infer_top,
    write_design_files,
)
from runner import (
    CORES_ENV,
    FlowError,
    DEFAULT_CORES,
    FINISH_REQUIRED,
    KIND_BY_SUFFIX,
    STAGES,
    STAGE_ARTIFACTS,
    can_export_gds,
    collect_artifacts,
    cores_from_environment,
    failure_detail,
    make_command,
    stage_gate,
    write_plan,
)

RTL = """\
module top (clk, rst_n, a, b, y);
  input clk;
  input rst_n;
  input [7:0] a;
  input [7:0] b;
  output [7:0] y;
  adder u_add (.a(a), .b(b), .y(y));
endmodule

module adder (a, b, y);
  input [7:0] a;
  input [7:0] b;
  output [7:0] y;
  assign y = a + b;
endmodule
"""


def write_rtl(root: Path, name: str = "top.v") -> Path:
    path = root / name
    path.write_text(RTL, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# inferring the design
# --------------------------------------------------------------------------

def test_the_top_module_is_the_one_nothing_instantiates():
    assert infer_top(RTL, "fallback") == "top"


def test_the_top_module_falls_back_to_the_requested_name():
    single = "module only_one (a, b);\ninput a;\ninput b;\nendmodule\n"
    assert infer_top(single, "only_one") == "only_one"


def test_rtl_without_a_module_is_refused():
    with pytest.raises(ValueError, match="module declaration"):
        infer_top("// nothing here\n", "x")


def test_the_clock_is_found_by_name_then_by_shape():
    assert infer_clock(RTL, "top") == "clk"
    odd = "module m (sysclk_i, d, q);\ninput sysclk_i;\ninput d;\noutput q;\nendmodule\n"
    assert infer_clock(odd, "m") == "sysclk_i"
    assert "clk" in CLOCK_CANDIDATES


# --------------------------------------------------------------------------
# the clock period unit -- the 1000x trap, execution side
# --------------------------------------------------------------------------

def test_nanosecond_platforms_use_the_period_unchanged():
    assert clock_period_in_platform_units(10.0, "nangate45") == pytest.approx(10.0)
    assert clock_period_in_platform_units(10.0, "sky130hd") == pytest.approx(10.0)


def test_picosecond_platforms_scale_the_period_up():
    """ASAP7's Liberty unit is ps, so a 10 ns request is written as 10000."""
    assert PLATFORM_TIME_UNIT_NS["asap7"] == pytest.approx(1e-3)
    assert clock_period_in_platform_units(10.0, "asap7") == pytest.approx(10000.0)


def test_the_written_config_carries_the_converted_period(tmp_path):
    rtl = write_rtl(tmp_path)
    config = write_design_files(
        workdir=tmp_path, rtl_path=rtl, design="top", platform="asap7",
        clock="clk", clock_period_ns=10.0, core_utilization_pct=40.0,
        place_density=0.6,
    )
    text = config.read_text(encoding="utf-8")
    assert "export CLOCK_PERIOD = 10000" in text
    sdc = (config.parent / "constraint.sdc").read_text(encoding="utf-8")
    assert "-period 10000" in sdc


# --------------------------------------------------------------------------
# the floorplan policy
# --------------------------------------------------------------------------

def test_the_default_uses_core_utilization(tmp_path):
    rtl = write_rtl(tmp_path)
    config = write_design_files(
        workdir=tmp_path, rtl_path=rtl, design="top", platform="sky130hd",
        clock="clk", clock_period_ns=10.0, core_utilization_pct=45.0,
        place_density=0.6,
    )
    text = config.read_text(encoding="utf-8")
    assert "export CORE_UTILIZATION = 45" in text
    assert "DIE_AREA" not in text
    assert "CORE_AREA" not in text


def test_a_minimum_die_size_emits_both_rectangles(tmp_path):
    """DIE_AREA alone makes ORFS stop at floorplan.

    It disables the utilisation-based sizing and still leaves no area for
    ``initialize_floorplan``, so emitting one without the other is a guaranteed
    floorplan failure on every non-nangate45 platform.
    """
    rtl = write_rtl(tmp_path)
    config = write_design_files(
        workdir=tmp_path, rtl_path=rtl, design="top", platform="sky130hd",
        clock="clk", clock_period_ns=10.0, core_utilization_pct=45.0,
        place_density=0.6, minimum_die_size_um=100.0,
    )
    text = config.read_text(encoding="utf-8")
    assert "export DIE_AREA = 0 0 100 100" in text
    assert "export CORE_AREA = 10 10 90 90" in text
    assert "CORE_UTILIZATION" not in text


def test_an_out_of_range_value_is_refused_through_the_real_argument(tmp_path):
    """The explicit argument is not a bypass.

    The frozen implementation validated only the tuning dictionary and then used
    the explicit arguments unchecked when it was absent, so a caller could pass
    an out-of-range utilization straight through the gate.  Merging the defaults
    into the validated set closes that.
    """
    rtl = write_rtl(tmp_path)
    with pytest.raises(ValueError, match="below its lower bound"):
        write_design_files(
            workdir=tmp_path, rtl_path=rtl, design="top", platform="nangate45",
            clock="clk", clock_period_ns=10.0, core_utilization_pct=1.0,
            place_density=0.6,
        )
    with pytest.raises(ValueError, match="calibrated range for asap7"):
        write_design_files(
            workdir=tmp_path, rtl_path=rtl, design="top", platform="asap7",
            clock="clk", clock_period_ns=10.0, core_utilization_pct=76.0,
            place_density=0.6,
        )


def test_an_unknown_tuning_parameter_is_refused(tmp_path):
    """A mistyped parameter must be reported, not silently ignored."""
    rtl = write_rtl(tmp_path)
    with pytest.raises(ValueError, match="unsupported ORFS tuning parameters"):
        write_design_files(
            workdir=tmp_path, rtl_path=rtl, design="top", platform="nangate45",
            clock="clk", clock_period_ns=10.0, core_utilization_pct=40.0,
            place_density=0.6, flow_parameters={"make_it_faster": 1},
        )


def test_tuned_parameters_reach_the_config_through_the_allowlist(tmp_path):
    """Names come from the parameter table, not from upper-casing a key."""
    rtl = write_rtl(tmp_path)
    config = write_design_files(
        workdir=tmp_path, rtl_path=rtl, design="top", platform="nangate45",
        clock="clk", clock_period_ns=10.0, core_utilization_pct=40.0,
        place_density=0.6,
        flow_parameters={"cts_cluster_size": 20, "gpl_timing_driven": 1},
    )
    text = config.read_text(encoding="utf-8")
    assert "export CTS_CLUSTER_SIZE = 20" in text
    assert "export GPL_TIMING_DRIVEN = 1" in text


def test_nangate45_gets_its_own_pdn_template(tmp_path):
    rtl = write_rtl(tmp_path)
    config = write_design_files(
        workdir=tmp_path, rtl_path=rtl, design="top", platform="nangate45",
        clock="clk", clock_period_ns=10.0, core_utilization_pct=40.0,
        place_density=0.6,
    )
    text = config.read_text(encoding="utf-8")
    assert "export PDN_TCL" in text
    pdn = config.parent / "pdn.tcl"
    assert pdn.is_file()
    assert "add_global_connection" in pdn.read_text(encoding="utf-8")


def test_other_platforms_get_no_generated_pdn(tmp_path):
    """The nangate45 template names metal layers; applying it elsewhere would
    produce a wrong power network rather than an error."""
    rtl = write_rtl(tmp_path)
    config = write_design_files(
        workdir=tmp_path, rtl_path=rtl, design="top", platform="sky130hd",
        clock="clk", clock_period_ns=10.0, core_utilization_pct=40.0,
        place_density=0.6,
    )
    assert "PDN_TCL" not in config.read_text(encoding="utf-8")
    assert not (config.parent / "pdn.tcl").exists()


def test_place_density_is_written_as_a_single_policy(tmp_path):
    rtl = write_rtl(tmp_path)
    config = write_design_files(
        workdir=tmp_path, rtl_path=rtl, design="top", platform="nangate45",
        clock="clk", clock_period_ns=10.0, core_utilization_pct=40.0,
        place_density=0.6,
    )
    assert "export PLACE_DENSITY = 0.6" in config.read_text(encoding="utf-8")

    config2 = write_design_files(
        workdir=tmp_path / "second", rtl_path=rtl, design="top",
        platform="nangate45", clock="clk", clock_period_ns=10.0,
        core_utilization_pct=40.0, place_density=0.6,
        flow_parameters={"place_density_lb_addon": 0.03},
    )
    text = config2.read_text(encoding="utf-8")
    # The addon policy wins, and the contradicting value is not left behind.
    assert "PLACE_DENSITY_LB_ADDON" in text
    assert "export PLACE_DENSITY " not in text


def test_the_rtl_is_staged_into_the_workspace(tmp_path):
    """The operator's ORFS tree is shared and must never be written to."""
    source_root = tmp_path / "source"
    source_root.mkdir()
    rtl = write_rtl(source_root)
    workdir = tmp_path / "work"
    workdir.mkdir()
    config = write_design_files(
        workdir=workdir, rtl_path=rtl, design="top", platform="nangate45",
        clock="clk", clock_period_ns=10.0, core_utilization_pct=40.0,
        place_density=0.6,
    )
    staged = workdir / "designs" / "src" / "top" / "top.v"
    assert staged.is_file()
    assert staged.read_text(encoding="utf-8") == RTL
    assert f"export VERILOG_FILES = {staged}" in config.read_text(encoding="utf-8")
    # The source is untouched.
    assert rtl.read_text(encoding="utf-8") == RTL


# --------------------------------------------------------------------------
# the invocation
# --------------------------------------------------------------------------

def test_the_make_command_carries_every_required_variable(tmp_path):
    command = make_command(
        config_path=tmp_path / "designs" / "nangate45" / "top" / "config.mk",
        workdir=tmp_path, flow_home=Path("/orfs/flow"),
        openroad_bin=Path("/tools/openroad"), yosys_bin=Path("/tools/yosys"),
        cores=8, target="place",
    )
    assert command[0] == "make"
    assert command[-1] == "place"
    joined = " ".join(command)
    assert f"DESIGN_CONFIG={tmp_path}/designs/nangate45/top/config.mk" in joined
    assert f"DESIGN_HOME={tmp_path}/designs" in joined
    assert f"WORK_HOME={tmp_path}" in joined
    assert "OPENROAD_EXE=/tools/openroad" in joined
    assert "YOSYS_EXE=/tools/yosys" in joined
    assert "NUM_CORES=8" in joined
    # Disabled on purpose: enabling them changes the runtime a candidate is
    # scored on, which would make runs incomparable.
    assert "EQUIVALENCE_CHECK=0" in command
    assert "LEC_CHECK=0" in command


def test_the_parallelism_bounds_are_enforced_not_clamped(monkeypatch):
    monkeypatch.setenv(CORES_ENV, "8")
    assert cores_from_environment() == 8
    monkeypatch.setenv(CORES_ENV, "0")
    with pytest.raises(FlowError, match="must be between"):
        cores_from_environment()
    monkeypatch.setenv(CORES_ENV, "999")
    with pytest.raises(FlowError, match="must be between"):
        cores_from_environment()
    monkeypatch.setenv(CORES_ENV, "many")
    with pytest.raises(FlowError, match="must be an integer"):
        cores_from_environment()


def test_the_default_parallelism_matches_the_recorded_default(monkeypatch):
    monkeypatch.delenv(CORES_ENV, raising=False)
    assert cores_from_environment() == DEFAULT_CORES


# --------------------------------------------------------------------------
# stage gating
# --------------------------------------------------------------------------

def test_the_stage_order_is_the_orfs_flow():
    assert STAGES == ("synth", "floorplan", "place", "cts", "route", "finish")


def test_synthesis_accepts_either_product(tmp_path):
    """Older ORFS revisions end synth at the netlist, newer ones also write the
    database.  Requiring the database rejects a valid flow before floorplan."""
    assert STAGE_ARTIFACTS["synth"] == ("1_synth.odb", "1_synth.v")
    results = tmp_path / "results" / "nangate45" / "top" / "base"
    results.mkdir(parents=True)
    assert stage_gate(tmp_path, "nangate45", "top", "synth") is not None
    (results / "1_synth.v").write_text("netlist\n", encoding="utf-8")
    assert stage_gate(tmp_path, "nangate45", "top", "synth") is None


def test_an_empty_product_does_not_satisfy_a_stage(tmp_path):
    results = tmp_path / "results" / "nangate45" / "top" / "base"
    results.mkdir(parents=True)
    (results / "2_floorplan.odb").write_text("", encoding="utf-8")
    gate = stage_gate(tmp_path, "nangate45", "top", "floorplan")
    assert gate is not None and "missing or empty" in gate


def test_finish_requires_the_full_signoff_set(tmp_path):
    results = tmp_path / "results" / "nangate45" / "top" / "base"
    results.mkdir(parents=True)
    (results / "6_final.odb").write_text("db\n", encoding="utf-8")
    gate = stage_gate(tmp_path, "nangate45", "top", "finish")
    assert gate is not None
    for name in FINISH_REQUIRED:
        assert name in gate
    for name in FINISH_REQUIRED:
        (results / name).write_text(f"{name}\n", encoding="utf-8")
    assert stage_gate(tmp_path, "nangate45", "top", "finish") is None


def test_gds_export_is_triggered_only_when_the_layout_is_missing(tmp_path):
    results = tmp_path / "results" / "nangate45" / "top" / "base"
    results.mkdir(parents=True)
    assert can_export_gds(tmp_path, "nangate45", "top") is False
    (results / "6_final.odb").write_text("db\n", encoding="utf-8")
    assert can_export_gds(tmp_path, "nangate45", "top") is True
    (results / "6_final.gds").write_text("gds\n", encoding="utf-8")
    assert can_export_gds(tmp_path, "nangate45", "top") is False


def test_the_failure_detail_is_the_last_real_error_line(tmp_path):
    log = tmp_path / "flow.log"
    log.write_text(
        "starting\n"
        "[ERROR] earlier problem that was handled\n"
        "more output\n"
        "Error: no floorplan initialization method specified\n"
        "trailing noise\n",
        encoding="utf-8",
    )
    assert failure_detail(log) == \
        "Error: no floorplan initialization method specified"


def test_a_log_without_an_error_yields_no_detail(tmp_path):
    log = tmp_path / "flow.log"
    log.write_text("all good\n", encoding="utf-8")
    assert failure_detail(log) is None


def test_a_missing_log_is_not_an_error(tmp_path):
    assert failure_detail(tmp_path / "absent.log") is None


# --------------------------------------------------------------------------
# evidence collection
# --------------------------------------------------------------------------

def test_empty_reports_are_absence_markers_not_artifacts(tmp_path):
    """A clean run writes empty DRC and antenna reports.

    Registering one would be a zero-length artifact, which the platform rejects
    as a protocol violation -- caused by the plugin, not by the run.
    """
    results = tmp_path / "results" / "nangate45" / "top" / "base"
    results.mkdir(parents=True)
    (results / "6_final.def").write_text("design\n", encoding="utf-8")
    (results / "5_route_drc.rpt").parent.mkdir(parents=True, exist_ok=True)
    reports = tmp_path / "reports" / "nangate45" / "top" / "base"
    reports.mkdir(parents=True)
    (reports / "5_route_drc.rpt").write_text("", encoding="utf-8")

    keys = {a["path"] for a in collect_artifacts(tmp_path, "nangate45", "top")}
    assert "results/nangate45/top/base/6_final.def" in keys
    assert "reports/nangate45/top/base/5_route_drc.rpt" not in keys


def test_artifact_kinds_come_from_the_suffix(tmp_path):
    results = tmp_path / "results" / "nangate45" / "top" / "base"
    results.mkdir(parents=True)
    for name in ("6_final.def", "6_final.gds", "6_final.v", "6_final.odb"):
        (results / name).write_text("x\n", encoding="utf-8")
    logs = tmp_path / "logs" / "nangate45" / "top" / "base"
    logs.mkdir(parents=True)
    (logs / "6_report.json").write_text("{}", encoding="utf-8")

    kinds = {a["path"]: a["kind"]
             for a in collect_artifacts(tmp_path, "nangate45", "top")}
    assert kinds["results/nangate45/top/base/6_final.def"] == "def"
    assert kinds["results/nangate45/top/base/6_final.gds"] == "gds"
    assert kinds["results/nangate45/top/base/6_final.v"] == "netlist"
    assert kinds["results/nangate45/top/base/6_final.odb"] == "odb"
    assert kinds["logs/nangate45/top/base/6_report.json"] == "report"
    assert KIND_BY_SUFFIX[".rpt"] == "report"


def test_collected_paths_are_relative_to_the_workspace(tmp_path):
    """Absolute paths would let an adapter declare an artifact outside its own
    workspace, which the platform refuses."""
    results = tmp_path / "results" / "nangate45" / "top" / "base"
    results.mkdir(parents=True)
    (results / "6_final.def").write_text("x\n", encoding="utf-8")
    for artifact in collect_artifacts(tmp_path, "nangate45", "top"):
        assert not artifact["path"].startswith("/")
        assert ".." not in artifact["path"].split("/")


def test_the_plan_records_what_the_evaluator_will_need(tmp_path):
    config = tmp_path / "designs" / "sky130hd" / "top" / "config.mk"
    config.parent.mkdir(parents=True)
    config.write_text("export DESIGN_NAME = top\n", encoding="utf-8")
    write_plan(
        tmp_path, run_id="run-1", design="top", platform="sky130hd",
        config_path=config, clock_period_ns=10.0, or_seed=3,
        target_stage="finish",
    )
    plan = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))
    assert plan["design"] == "top"
    assert plan["request"]["platform"] == "sky130hd"
    assert plan["request"]["or_seed"] == 3
    assert plan["request"]["clock_period_ns"] == 10.0
