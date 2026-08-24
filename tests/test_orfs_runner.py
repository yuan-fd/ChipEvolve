from __future__ import annotations

from pathlib import Path
import json

from openroad_platform_contracts import RunRequest, RunStage, RunStatus
from openroad_platform_execution import ORFSRunner


def _fake_runtime(tmp_path: Path):
    orfs = tmp_path / "orfs"
    flow = orfs / "flow"
    flow.mkdir(parents=True)
    (flow / "Makefile").write_text(
        "OUT := $(WORK_HOME)/results/nangate45/top/base\n"
        "define emit\n\n\tmkdir -p $(OUT)\n\tprintf odb > $(OUT)/$(1)\nendef\n"
        "synth:\n\t$(call emit,1_synth.odb)\n"
        "floorplan:\n\t$(call emit,2_floorplan.odb)\n"
        "place:\n\t$(call emit,3_place.odb)\n"
        "cts:\n\t$(call emit,4_cts.odb)\n"
        "route:\n\t$(call emit,5_route.odb)\n"
        "finish:\n\t$(call emit,6_final.odb)\n"
        "\tprintf def > $(OUT)/6_final.def\n"
        "\tprintf netlist > $(OUT)/6_final.v\n"
        "\tprintf gds > $(OUT)/6_final.gds\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("openroad", "yosys"):
        binary = bin_dir / name
        binary.write_text("#!/bin/sh\nprintf 'fake-tool 1.0\\n'\n")
        binary.chmod(0o755)
    return orfs, bin_dir


def test_runner_executes_stages_and_applies_finish_hard_gate(tmp_path):
    orfs, bin_dir = _fake_runtime(tmp_path)
    rtl = tmp_path / "top.v"
    rtl.write_text("module top(input clk, input a, output y); assign y = a; endmodule\n")
    runner = ORFSRunner(
        orfs_root=orfs,
        work_root=tmp_path / "runs",
        openroad_bin=bin_dir / "openroad",
        yosys_bin=bin_dir / "yosys",
    )
    plan = runner.prepare(RunRequest(rtl_path=str(rtl), top="top"))
    config = Path(plan.config_path).read_text()
    assert "ABC_SPEED" not in config
    assert "ABC_POWER" not in config

    result = runner.run(plan)
    assert result.status is RunStatus.SUCCEEDED
    assert len(result.stages) == 6
    assert {artifact.kind.value for artifact in result.artifacts} >= {"odb", "def", "gds"}
    assert result.milestones == {
        "synthesizable": True,
        "functionally_verified": False,
        "implementation_valid": True,
        "gds_complete": True,
    }
    assert (Path(plan.workdir) / "analysis/report.json").is_file()
    assert (Path(plan.workdir) / "run_result.json").is_file()


def test_runner_fails_when_process_succeeds_without_required_artifact(tmp_path):
    orfs, bin_dir = _fake_runtime(tmp_path)
    makefile = orfs / "flow/Makefile"
    makefile.write_text("synth:\n\t@true\n")
    rtl = tmp_path / "top.v"
    rtl.write_text("module top; endmodule\n")
    runner = ORFSRunner(
        orfs_root=orfs,
        work_root=tmp_path / "runs",
        openroad_bin=bin_dir / "openroad",
        yosys_bin=bin_dir / "yosys",
    )
    request = RunRequest(rtl_path=str(rtl), top="top", target_stage="synth")
    plan = runner.prepare(RunRequest.from_dict(request.to_dict()))
    result = runner.run(plan)
    assert result.status is RunStatus.FAILED
    assert "Required artifacts" in result.error
    assert (Path(plan.workdir) / "analysis/flow_error.log").is_file()


def test_finish_failure_can_export_gds_without_claiming_valid_implementation(tmp_path):
    orfs, bin_dir = _fake_runtime(tmp_path)
    (orfs / "flow/Makefile").write_text(
        "OUT := $(WORK_HOME)/results/nangate45/top/base\n"
        "synth:\n\tmkdir -p $(OUT)\n\tprintf odb > $(OUT)/1_synth.odb\n"
        "floorplan:\n\tprintf odb > $(OUT)/2_floorplan.odb\n"
        "place:\n\tprintf odb > $(OUT)/3_place.odb\n"
        "cts:\n\tprintf odb > $(OUT)/4_cts.odb\n"
        "route:\n\tprintf odb > $(OUT)/5_route.odb\n"
        "finish:\n\tprintf odb > $(OUT)/6_final.odb\n"
        "\tprintf def > $(OUT)/6_final.def\n"
        "\tprintf netlist > $(OUT)/6_final.v\n"
        "\t@false\n"
        "gds:\n\tprintf gds > $(OUT)/6_final.gds\n"
    )
    rtl = tmp_path / "top.v"
    rtl.write_text("module top; endmodule\n")
    runner = ORFSRunner(
        orfs_root=orfs,
        work_root=tmp_path / "runs",
        openroad_bin=bin_dir / "openroad",
        yosys_bin=bin_dir / "yosys",
    )
    result = runner.run(runner.prepare(RunRequest(rtl_path=str(rtl), top="top")))
    assert result.status is RunStatus.FAILED
    assert result.milestones["implementation_valid"] is False
    assert result.milestones["gds_complete"] is True
    assert "GDS export succeeded" in result.error


def test_explicit_minimum_die_area_excludes_utilization_floorplan_mode(tmp_path):
    orfs, bin_dir = _fake_runtime(tmp_path)
    rtl = tmp_path / "tiny.v"
    rtl.write_text("module tiny(input a, output y); assign y=~a; endmodule\n")
    runner = ORFSRunner(
        orfs_root=orfs, work_root=tmp_path / "runs",
        openroad_bin=bin_dir / "openroad", yosys_bin=bin_dir / "yosys",
    )
    request = RunRequest(
        rtl_path=str(rtl), top="tiny", target_stage=RunStage.SYNTH,
        minimum_die_size_um=20,
    )
    plan = runner.prepare(request)
    config = Path(plan.config_path).read_text()
    assert "DIE_AREA = 0 0 20 20" in config
    assert "CORE_AREA = 2 2 18 18" in config
    assert "CORE_UTILIZATION" not in config


def test_default_non_nangate_floorplan_has_a_complete_initialization_policy(tmp_path):
    """Regression: DIE_AREA alone leaves ORFS floorplan undefined."""
    orfs, bin_dir = _fake_runtime(tmp_path)
    rtl = tmp_path / "tiny.v"
    rtl.write_text("module tiny(input a, output y); assign y=~a; endmodule\n")
    runner = ORFSRunner(
        orfs_root=orfs, work_root=tmp_path / "runs",
        openroad_bin=bin_dir / "openroad", yosys_bin=bin_dir / "yosys",
    )
    plan = runner.prepare(RunRequest(
        rtl_path=str(rtl), top="tiny", platform="sky130hd",
        core_utilization_pct=37, target_stage=RunStage.SYNTH,
    ))
    config = Path(plan.config_path).read_text()
    assert "CORE_UTILIZATION = 37" in config
    assert "DIE_AREA" not in config


def test_finish_json_fallback_preserves_terminal_qor_without_analysis_package(tmp_path):
    orfs, bin_dir = _fake_runtime(tmp_path)
    rtl = tmp_path / "top.v"; rtl.write_text("module top; endmodule\n")
    runner = ORFSRunner(orfs_root=orfs, work_root=tmp_path / "runs",
                        openroad_bin=bin_dir / "openroad", yosys_bin=bin_dir / "yosys")
    plan = runner.prepare(RunRequest(rtl_path=str(rtl), top="top"))
    report = Path(plan.workdir) / "logs/nangate45/top/base/6_report.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({"finish__design__instance__area": 12.5,
                                  "finish__timing__setup__ws": 0.2,
                                  "finish__power__total": 0.004}), encoding="utf-8")
    values = {metric.name: metric.value for metric in runner._collect_finish_metrics_fallback(plan)}
    assert values == {"finish__design__instance__area": 12.5,
                      "finish__timing__setup__ws": 0.2,
                      "finish__power__total": 0.004}
