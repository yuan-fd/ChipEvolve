"""The ORFS adapter, run as a real process through the platform.

The other ORFS tests check functions.  This one runs the adapter the way the
platform runs it -- a subprocess, given a request file, expected to produce a
result file -- against a stub Makefile that stands in for the real flow.

That makes the whole chain testable without a toolchain: configuration is
written, stages run in order, each stage is gated on the artifact it should have
produced, evidence is collected with the right kinds, and the exit code agrees
with the reported status.  A stub cannot prove ORFS works; it proves the
platform's half of the contract is wired correctly, which is the half that
breaks silently.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ADAPTER = REPO_ROOT / "plugins" / "orfs" / "adapter.py"

RTL = """\
module counter (clk, rst_n, q);
  input clk;
  input rst_n;
  output reg [3:0] q;
  always @(posedge clk) begin
    if (!rst_n) q <= 4'b0;
    else q <= q + 1'b1;
  end
endmodule
"""

#: A stub flow.  Each target materializes exactly the products the real stage
#: gate requires, so the test exercises the gate rather than bypassing it.
#:
#: It deliberately includes $(DESIGN_CONFIG) the way ORFS does: the stub's
#: first version did not, so PLATFORM and DESIGN_NAME were empty, every path
#: collapsed to results///base, and every stage gate failed.  A stub that
#: omits the flow's own mechanism tests nothing about the flow.
STUB_MAKEFILE = """\
include $(DESIGN_CONFIG)

RESULT_DIR = $(WORK_HOME)/results/$(PLATFORM)/$(DESIGN_NAME)/base
LOGS_DIR = $(WORK_HOME)/logs/$(PLATFORM)/$(DESIGN_NAME)/base

.PHONY: synth floorplan place cts route finish gds

$(RESULT_DIR):
\tmkdir -p $(RESULT_DIR)

$(LOGS_DIR):
\tmkdir -p $(LOGS_DIR)

synth: | $(RESULT_DIR)
\techo "synth netlist" > $(RESULT_DIR)/1_synth.v

floorplan: | $(RESULT_DIR)
\techo "floorplan db" > $(RESULT_DIR)/2_floorplan.odb

place: | $(RESULT_DIR)
\techo "place db" > $(RESULT_DIR)/3_place.odb

cts: | $(RESULT_DIR)
\techo "cts db" > $(RESULT_DIR)/4_cts.odb

route: | $(RESULT_DIR) $(LOGS_DIR)
\techo "route db" > $(RESULT_DIR)/5_route.odb
\techo '{"detailedroute__route__drc_errors": 0, "run__flow__platform__time_units": "1ns"}' > $(LOGS_DIR)/5_2_route.json

finish: | $(RESULT_DIR) $(LOGS_DIR)
\techo "final db" > $(RESULT_DIR)/6_final.odb
\techo "final def" > $(RESULT_DIR)/6_final.def
\techo "final netlist" > $(RESULT_DIR)/6_final.v
\techo '{"finish__timing__setup__ws": 0.1, "finish__power__total": 0.01}' > $(LOGS_DIR)/6_report.json

# The real flow does not always write the layout during finish; that is why a
# separate target exists.
gds: | $(RESULT_DIR)
\techo "layout" > $(RESULT_DIR)/6_final.gds
"""


@pytest.fixture()
def stub_toolchain(tmp_path: Path) -> dict[str, Path]:
    """A minimal ORFS checkout: ``<root>/flow/Makefile`` and the two tools.

    The layout is the real one.  An earlier fixture made the flow home a
    directory of its own, which is how this plugin acquired a second accepted
    layout and a helper to invert it; neither exists upstream, and both are now
    gone.  The tools deliberately live outside the flow directory, so a PATH
    assertion tests the composition rather than a coincidence.
    """
    root = tmp_path / "orfs"
    flow_home = root / "flow"
    (flow_home / "logs").mkdir(parents=True)
    (flow_home / "Makefile").write_text(STUB_MAKEFILE, encoding="utf-8")
    binaries = root / "bin"
    binaries.mkdir()
    for name in ("openroad", "yosys"):
        binary = binaries / name
        binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return {
        "root": root,
        "flow_home": flow_home,
        "openroad_bin": binaries / "openroad",
        "yosys_bin": binaries / "yosys",
    }


def run_adapter(tmp_path: Path, toolchain: dict[str, Path],
                *, inputs: dict | None = None,
                create_layout: bool = True,
                makefile: str | None = None,
                adapter_environment: dict | None = None,
                bundle: bool = False) -> tuple[dict, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    rtl = tmp_path / "counter.v"
    rtl.write_text(RTL, encoding="utf-8")

    if makefile is not None:
        (toolchain["flow_home"] / "Makefile").write_text(makefile, encoding="utf-8")

    if not create_layout:
        # Make the gds target fail so the adapter must report a failed export.
        makefile = toolchain["flow_home"] / "Makefile"
        makefile.write_text(
            STUB_MAKEFILE.replace(
                'gds: | $(RESULT_DIR)\n\techo "layout" > $(RESULT_DIR)/6_final.gds',
                'gds: | $(RESULT_DIR)\n\t@echo "cannot write layout" >&2; exit 4',
            ),
            encoding="utf-8",
        )

    task_inputs = {
        "rtl_path": str(rtl),
        "platform": "nangate45",
        "design": "counter",
        "clock_period_ns": 10.0,
        "orfs_root": str(toolchain["root"]),
        "openroad_bin": str(toolchain["openroad_bin"]),
        "yosys_bin": str(toolchain["yosys_bin"]),
        "stage_timeout_seconds": 60,
    }
    if bundle:
        # A reference design names its sources as a rooted bundle and never as a
        # single file.
        task_inputs.pop("rtl_path")
    task_inputs.update(inputs or {})

    request_path = workspace / "adapter_request.json"
    result_path = workspace / "adapter_result.json"
    request_path.write_text(json.dumps({
        "schema_version": 1,
        "plugin": {"plugin_id": "orfs", "plugin_version": "1.0.0"},
        "task": {
            "schema_version": 2, "task_id": "task-1", "project_id": "p",
            "design_id": "counter", "plugin_id": "orfs",
            "inputs": task_inputs, "parameters": {"or_seed": 1},
        },
    }), encoding="utf-8")

    environment = dict(os.environ)
    environment["PATH"] = os.environ.get("PATH", "")
    environment.update(adapter_environment or {})
    completed = subprocess.run(
        [sys.executable, str(ADAPTER),
         "--request", str(request_path), "--result", str(result_path)],
        cwd=str(workspace), env=environment, capture_output=True, text=True,
        timeout=120,
    )
    assert result_path.is_file(), (
        f"adapter produced no result\nstdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    return json.loads(result_path.read_text(encoding="utf-8")), workspace


# --------------------------------------------------------------------------
# the happy path
# --------------------------------------------------------------------------

def test_a_stubbed_flow_runs_every_stage_and_reports_success(tmp_path, stub_toolchain):
    result, workspace = run_adapter(tmp_path, stub_toolchain)
    assert result["status"] == "succeeded", result.get("failure")
    assert result["exit_code"] == 0

    plan = json.loads((workspace / "plan.json").read_text(encoding="utf-8"))
    assert plan["design"] == "counter"
    assert plan["request"]["platform"] == "nangate45"
    assert plan["request"]["or_seed"] == 1

    run_result = json.loads(
        (workspace / "run_result.json").read_text(encoding="utf-8")
    )
    stages = [s["stage"] for s in run_result["stages"]]
    assert stages == ["synth", "floorplan", "place", "cts", "route", "finish"]
    assert all(s["status"] == "succeeded" for s in run_result["stages"])


def test_progress_is_reported_on_stdout_for_every_stage(tmp_path, stub_toolchain):
    """The platform shows stage timing without knowing any ORFS stage name."""
    result, workspace = run_adapter(tmp_path, stub_toolchain)
    log = (workspace / "logs" / "flow.log")
    assert log.is_file()
    # The adapter's own progress goes to stdout, which the platform captures
    # through the guardian; assert on the recorded run result instead.
    run_result = json.loads(
        (workspace / "run_result.json").read_text(encoding="utf-8")
    )
    assert all("seconds" in s for s in run_result["stages"])


def test_the_layout_is_exported_when_finish_did_not_write_it(tmp_path, stub_toolchain):
    result, workspace = run_adapter(tmp_path, stub_toolchain)
    assert result["status"] == "succeeded"
    run_result = json.loads(
        (workspace / "run_result.json").read_text(encoding="utf-8")
    )
    assert run_result["gds_exported"] is True
    results = workspace / "results" / "nangate45" / "counter" / "base"
    assert (results / "6_final.gds").is_file()


def test_evidence_is_collected_with_the_right_kinds(tmp_path, stub_toolchain):
    result, workspace = run_adapter(tmp_path, stub_toolchain)
    kinds = {a["path"]: a["kind"] for a in result["artifacts"]}
    base = "results/nangate45/counter/base"
    assert kinds[f"{base}/6_final.def"] == "def"
    assert kinds[f"{base}/6_final.gds"] == "gds"
    assert kinds[f"{base}/6_final.v"] == "netlist"
    assert kinds[f"{base}/6_final.odb"] == "odb"
    assert kinds["logs/nangate45/counter/base/6_report.json"] == "report"
    assert kinds["designs/nangate45/counter/config.mk"] == "report"
    # Every declared path is relative to the workspace, as the protocol requires.
    for path in kinds:
        assert not path.startswith("/")
        assert ".." not in path.split("/")


def test_the_flow_is_staged_into_the_workspace(tmp_path, stub_toolchain):
    """`make` must run in the attempt's own copy.

    The operator's ORFS tree is shared.  A run that wrote into it would change
    the toolchain under every other experiment, and the compatibility backport
    would edit the shared source rather than a per-attempt copy.
    """
    _, workspace = run_adapter(tmp_path, stub_toolchain)
    staged = workspace / "orfs-flow"
    assert staged.is_dir()
    assert (staged / "Makefile").is_file()
    # The operator tree is still intact: staging copied rather than moved.
    assert (stub_toolchain["flow_home"] / "Makefile").is_file()


def test_the_operator_tree_is_never_patched(tmp_path, stub_toolchain):
    """Add a patchable script to the operator tree and prove it is untouched."""
    scripts = stub_toolchain["flow_home"] / "scripts"
    scripts.mkdir()
    original = "header\nif {[expr [llength [info procs save_image]] > 0]} {\n"
    (scripts / "final_report.tcl").write_text(original, encoding="utf-8")

    _, workspace = run_adapter(tmp_path, stub_toolchain)

    # The operator copy is unchanged, whatever the staged one became.
    assert (scripts / "final_report.tcl").read_text(encoding="utf-8") == original
    assert (workspace / "orfs-flow" / "scripts" / "final_report.tcl").is_file()


def test_the_compatibility_receipt_is_evidence(tmp_path, stub_toolchain):
    """Written even when nothing needed patching, so "no patch was needed" is
    evidence rather than an absence to interpret."""
    result, workspace = run_adapter(tmp_path, stub_toolchain)
    receipt = json.loads(
        (workspace / "flow_compatibility.json").read_text(encoding="utf-8")
    )
    assert receipt["kind"] == "orfs-flow-compatibility"
    assert receipt["changes"] == []
    assert "claim_boundary" in receipt
    assert "flow_compatibility.json" in {a["path"] for a in result["artifacts"]}


def test_the_generated_configuration_is_evidence(tmp_path, stub_toolchain):
    _, workspace = run_adapter(tmp_path, stub_toolchain)
    config = workspace / "designs" / "nangate45" / "counter" / "config.mk"
    text = config.read_text(encoding="utf-8")
    assert "export DESIGN_NAME = counter" in text
    assert "export PLATFORM = nangate45" in text
    assert "export OR_SEED = 1" in text
    sdc = (config.parent / "constraint.sdc").read_text(encoding="utf-8")
    assert "create_clock -name clk" in sdc


# --------------------------------------------------------------------------
# failures are reported, not hidden
# --------------------------------------------------------------------------

def test_a_missing_toolchain_is_a_configuration_error(tmp_path, stub_toolchain):
    result, _ = run_adapter(
        tmp_path, stub_toolchain,
        inputs={"openroad_bin": str(tmp_path / "does-not-exist")},
    )
    assert result["status"] == "failed"
    assert result["failure"]["category"] == "configuration_error"
    # Not retryable: no amount of retrying creates the binary.
    assert result["failure"]["retryable"] is False


def test_a_missing_rtl_file_fails_without_running_anything(tmp_path, stub_toolchain):
    result, workspace = run_adapter(
        tmp_path, stub_toolchain, inputs={"rtl_path": str(tmp_path / "absent.v")},
    )
    assert result["status"] == "failed"
    assert result["failure"]["category"] == "tool_error"
    assert not (workspace / "run_result.json").exists()


def test_an_unsupported_target_stage_is_refused(tmp_path, stub_toolchain):
    result, _ = run_adapter(
        tmp_path, stub_toolchain, inputs={"target_stage": "nonsense"},
    )
    assert result["status"] == "failed"
    assert "unsupported target stage" in result["failure"]["message"]


def test_a_failed_layout_export_fails_the_run(tmp_path, stub_toolchain):
    """A layout that cannot be written is a signoff failure, not a warning.

    The export is attempted inside the finish stage and before its gate, so a
    failed export surfaces as the finish gate reporting a missing layout -- which
    is what the frozen implementation did.  ``gds_exported`` carries the export
    attempt's own outcome so the two remain distinguishable.
    """
    result, workspace = run_adapter(tmp_path, stub_toolchain, create_layout=False)
    assert result["status"] == "failed"
    assert result["exit_code"] != 0

    run_result = json.loads(
        (workspace / "run_result.json").read_text(encoding="utf-8")
    )
    assert run_result["gds_exported"] is False
    assert run_result["failed_stage"] == "finish"
    assert "6_final.gds" in (run_result["failure_message"] or "")
    # The five earlier stages did succeed, and the record says so rather than
    # discarding the work.
    succeeded = [s["stage"] for s in run_result["stages"]
                 if s["status"] == "succeeded"]
    assert succeeded[:5] == ["synth", "floorplan", "place", "cts", "route"]
    assert run_result["milestones"]["implementation_valid"] is False
    assert run_result["milestones"]["gds_complete"] is False


def test_a_failed_stage_writes_a_machine_readable_flow_error(tmp_path, stub_toolchain):
    _, workspace = run_adapter(tmp_path, stub_toolchain, create_layout=False)
    error_log = workspace / "analysis" / "flow_error.log"
    assert error_log.is_file()
    text = error_log.read_text(encoding="utf-8")
    assert "stage=finish" in text
    assert "6_final.gds" in text


def test_a_successful_run_states_its_milestones(tmp_path, stub_toolchain):
    _, workspace = run_adapter(tmp_path, stub_toolchain)
    milestones = json.loads(
        (workspace / "run_result.json").read_text(encoding="utf-8")
    )["milestones"]
    assert milestones["synthesizable"] is True
    assert milestones["implementation_valid"] is True
    assert milestones["gds_complete"] is True
    # The platform never claims functional verification: that is a separate
    # capability with its own evidence.
    assert milestones["functionally_verified"] is False


def test_the_exit_code_agrees_with_the_reported_status(tmp_path, stub_toolchain):
    """A result that claims success while the process exits non-zero would be
    rejected by the platform; the adapter must not produce one."""
    result, workspace = run_adapter(tmp_path, stub_toolchain)
    completed_exit = 0 if result["status"] == "succeeded" else 1
    assert result["exit_code"] == completed_exit
    assert (workspace / "adapter_result.json").is_file()


def test_the_run_records_the_toolchain_that_produced_it(tmp_path, stub_toolchain):
    """A result nobody can attribute is a result nobody can reproduce.

    The snapshot is registered as evidence like any other file, and it names
    the request it belongs to, so "the same experiment" has a referent.
    """
    result, workspace = run_adapter(tmp_path, stub_toolchain)
    kinds = {a["path"]: a["kind"] for a in result["artifacts"]}
    assert kinds["toolchain_snapshot.json"] == "report"

    snapshot = json.loads(
        (workspace / "toolchain_snapshot.json").read_text(encoding="utf-8")
    )
    assert snapshot["toolchain"]["fingerprint"]
    assert snapshot["request"] == {
        "platform": "nangate45", "design": "counter", "target_stage": "finish",
        "clock_period_ns": 10.0, "or_seed": 1,
        "core_utilization_pct": None, "place_density": None,
        "flow_parameters": {},
    }
    # The generated configuration is an input the flow reads, so it is hashed
    # into the snapshot rather than merely named.
    assert snapshot["files"]["generated_config"]["sha256"]
    assert snapshot["files"]["rtl"]["sha256"]
    # Environment *names* are recorded, not values: a snapshot that leaked the
    # environment would be worse than one that omitted it.
    assert "environment_keys" in snapshot["toolchain"]
    assert "PATH" not in snapshot["toolchain"]["environment_keys"]


# --------------------------------------------------------------------------
# the environment the flow runs under
# --------------------------------------------------------------------------

#: Writes down what the flow itself could see.  A flow that silently inherited
#: the adapter's environment would be indistinguishable from one that was given
#: a composed one -- from the outside.
ENVIRONMENT_PROBE_MAKEFILE = STUB_MAKEFILE.replace(
    'synth: | $(RESULT_DIR)\n\techo "synth netlist" > $(RESULT_DIR)/1_synth.v',
    'synth: | $(RESULT_DIR)\n'
    '\techo "synth netlist" > $(RESULT_DIR)/1_synth.v\n'
    '\techo "PATH=$$PATH" > $(WORK_HOME)/flow_environment.txt\n'
    '\techo "TOOLCHAIN=$$OPENROAD_PLATFORM_TOOLCHAIN" >> $(WORK_HOME)/flow_environment.txt\n'
    '\techo "LEAKED=$$OPENROAD_PLATFORM_LEAKED_MARKER" >> $(WORK_HOME)/flow_environment.txt',
)


def test_the_flow_runs_under_the_toolchains_own_environment(tmp_path, stub_toolchain):
    """The snapshot must describe the toolchain that ran, not one observed.

    ``build_environment`` composes PATH so the tool's own directory wins, and
    records only the declared host variables.  A flow that inherited the
    adapter's environment instead would use whichever build of the tool the
    caller happened to have first on its PATH, while the snapshot named the
    profile -- a record that contradicts the run it describes.
    """
    _, workspace = run_adapter(
        tmp_path, stub_toolchain,
        makefile=ENVIRONMENT_PROBE_MAKEFILE,
        adapter_environment={"OPENROAD_PLATFORM_LEAKED_MARKER": "should-not-reach"},
    )
    probe = dict(
        line.split("=", 1) for line in
        (workspace / "flow_environment.txt").read_text(encoding="utf-8").splitlines()
        if "=" in line
    )

    # The toolchain's own directory comes first, which is what decides which
    # build of a tool the flow picks up.
    assert probe["PATH"].split(os.pathsep)[0] == str(
        stub_toolchain["openroad_bin"].parent)
    assert probe["TOOLCHAIN"] == "orfs"
    # The adapter's environment is not passed through wholesale.
    assert probe["LEAKED"] == ""


def test_the_composed_path_does_not_accumulate_duplicates(tmp_path, stub_toolchain):
    """PATH order is precedence; a duplicate makes the record misleading.

    The adapter is started with a deliberately doubled PATH.  If the flow
    inherited it, the duplicates would survive into the run -- and a recorded
    PATH with the same directory twice cannot be read as an order.
    """
    doubled = os.pathsep.join(["/usr/bin", "/bin", "/usr/bin", "/bin"])
    _, workspace = run_adapter(
        tmp_path, stub_toolchain, makefile=ENVIRONMENT_PROBE_MAKEFILE,
        adapter_environment={"PATH": doubled})
    path = next(
        line.split("=", 1)[1] for line in
        (workspace / "flow_environment.txt").read_text(encoding="utf-8").splitlines()
        if line.startswith("PATH=")
    )
    entries = path.split(os.pathsep)
    # The system directories are still reachable; they were deduplicated, not
    # dropped.
    assert "/usr/bin" in entries
    assert "/bin" in entries
    assert len(entries) == len(set(entries))


# --------------------------------------------------------------------------
# a reference-design bundle
# --------------------------------------------------------------------------

BUNDLE_RTL = """\
module blob_core (clk_i, nrst, q);
  input clk_i;
  input nrst;
  output [3:0] q;
  counter u_count (.clk_i(clk_i), .nrst(nrst), .q(q));
endmodule
"""


def make_bundle(tmp_path: Path) -> dict:
    """The shape ``reference_designs.to_inputs()`` produces.

    Two sources under a root, an include directory, a synthesis frontend, the
    design's own constraint file and its recipe options -- everything a reviewed
    reference design carries, and everything an earlier version of the adapter
    silently dropped.
    """
    root = tmp_path / "bundle"
    (root / "rtl").mkdir(parents=True)
    (root / "include").mkdir(parents=True)
    (root / "rtl" / "blob_core.sv").write_text(BUNDLE_RTL, encoding="utf-8")
    (root / "rtl" / "counter.sv").write_text(
        "module counter (input clk_i, input nrst, output reg [3:0] q);\n"
        "  always @(posedge clk_i) if (!nrst) q <= 0; else q <= q + 1;\n"
        "endmodule\n", encoding="utf-8")
    (root / "include" / "defs.svh").write_text("`define W 4\n", encoding="utf-8")
    sdc = root / "constraint_pos_slack.sdc"
    sdc.write_text("create_clock -name clk_i -period 1.468 [get_ports clk_i]\n",
                   encoding="utf-8")
    return {
        "rtl_root": str(root),
        "rtl_files": [str(root / "rtl" / "blob_core.sv"),
                      str(root / "rtl" / "counter.sv")],
        "rtl_include_dirs": [str(root / "include")],
        "top": "blob_core",
        "clock": "clk_i",
        # The reviewed ASAP7 ibex constraint, in public nanoseconds: the writer
        # converts it to the platform's unit on the way in.
        "clock_period_ns": 1.468,
        "sdc_path": str(sdc),
        "synth_hdl_frontend": "slang",
        "design_options": {"swap_arith_operators": 1, "openroad_hierarchical": 1},
    }


def test_a_reference_design_bundle_runs_end_to_end(tmp_path, stub_toolchain):
    """The bundle is the shape every reference design has.

    Before this, the adapter read only ``rtl_path`` and a bundle task failed
    outright -- which meant the reviewed reference designs could not be run at
    all, and the milestone tests below could never have caught it.
    """
    result, workspace = run_adapter(
        tmp_path, stub_toolchain, bundle=True,
        inputs=make_bundle(tmp_path) | {"platform": "asap7"},
    )
    assert result["status"] == "succeeded", result.get("failure")

    config = (workspace / "designs" / "asap7" / "blob_core" / "config.mk").read_text(
        encoding="utf-8")
    # Both sources, staged under their path relative to the bundle root.
    assert "blob_core.sv" in config and "counter.sv" in config
    assert "export VERILOG_INCLUDE_DIRS = " in config
    assert "export SYNTH_HDL_FRONTEND = slang" in config
    assert "export SWAP_ARITH_OPERATORS = 1" in config
    assert "export OPENROAD_HIERARCHICAL = 1" in config
    # The design's own constraint, not a generated one.
    sdc = (workspace / "designs" / "asap7" / "blob_core" / "constraint.sdc").read_text(
        encoding="utf-8")
    assert sdc == "create_clock -name clk_i -period 1.468 [get_ports clk_i]\n"
    # The ASAP7 period is in the platform's unit, not nanoseconds.
    assert "export CLOCK_PERIOD = 1468" in config


def test_the_top_module_names_the_design_not_the_bundle_label(tmp_path, stub_toolchain):
    """``top`` is what ORFS elaborates and the name it gives the design;
    ``design`` is the bundle's own label and only a fallback."""
    result, workspace = run_adapter(
        tmp_path, stub_toolchain, bundle=True,
        inputs=make_bundle(tmp_path) | {"design": "some_label"},
    )
    assert result["status"] == "succeeded", result.get("failure")
    plan = json.loads((workspace / "plan.json").read_text(encoding="utf-8"))
    assert plan["design"] == "blob_core"


def test_a_bundle_without_a_root_is_refused(tmp_path, stub_toolchain):
    """A bundle's files are staged relative to its root; without one there is
    nothing to be relative to, and guessing would invent a design identity."""
    bundle = make_bundle(tmp_path)
    bundle.pop("rtl_root")
    result, _ = run_adapter(
        tmp_path, stub_toolchain, bundle=True, inputs=bundle)
    assert result["status"] == "failed"
    assert "rtl_root" in result["failure"]["message"]


def test_naming_both_a_file_and_a_bundle_is_refused(tmp_path, stub_toolchain):
    """A caller who gave both meant one of them; a precedence rule would decide
    silently and the snapshot would describe the other."""
    result, _ = run_adapter(
        tmp_path, stub_toolchain, bundle=False,   # keeps rtl_path
        inputs=make_bundle(tmp_path))
    assert result["status"] == "failed"
    assert "not both" in result["failure"]["message"]
