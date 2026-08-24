from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path

import pytest

from openroad_platform_contracts import RuntimeStatus
from openroad_platform_execution import (
    PluginRegistry, TaiWeiToolchainProfile, build_taiwei_task,
    taiwei_plugin_manifest, taiwei_technology_profiles,
)
from openroad_platform_scheduler import RuntimeStore, WorkflowRuntime


def commit_repo(path: Path, files: dict[str, str]) -> str:
    path.mkdir(parents=True)
    for name, content in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        if name.endswith((".py", ".sh")):
            target.chmod(0o755)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "-qm", "fixture"], check=True)
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()


def fixture_environment(tmp_path: Path, case: str = "gcd", tech: str = "asap7_3D"):
    source = tmp_path / "taiwei"
    script = f'''#!/usr/bin/env python3
import argparse,json,sys
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument("--flow"); p.add_argument("--tech"); p.add_argument("--case"); p.add_argument("--run-only",action="store_true"); p.add_argument("--status-interval"); a=p.parse_args()
assert (a.flow,a.tech,a.case)==("ord","{tech}","{case}"), (a.flow,a.tech,a.case)
for name,data in (("reports/openroad_eval.json",json.dumps({{"wns_ns":0.1,"hbt_count":4}})),("logs/final_summary.txt","ok\\n"),("results/final.gds","GDSII"),("results/6_final.def","DEF"),("results/6_final.odb","ODB"),("results/6_final.v","module {case}; endmodule\\n"),("reports/final_3d.png","PNG"),("platforms/nangate45/gds/library.gds","NOT-A-RUN-RESULT")):
 path=Path(name); path.parent.mkdir(parents=True,exist_ok=True); path.write_text(data)
'''
    run_sh = ("#!/usr/bin/env bash\nset -euo pipefail\n"
              'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
              'exec bash "${SCRIPT_DIR}/../../../common/run_case.sh" ord "${SCRIPT_DIR}"\n')
    eval_sh = ("#!/usr/bin/env bash\nset -euo pipefail\n"
               'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
               'exec bash "${SCRIPT_DIR}/../../../common/eval_case.sh" ord "${SCRIPT_DIR}"\n')
    # Engine-like tree: shipped case dispatch + design config templates.
    files = {
        "run_experiments.py": script,
        "test/common/run_case.sh": "#!/usr/bin/env bash\nexit 0\n",
        "test/common/eval_case.sh": "#!/usr/bin/env bash\nexit 0\n",
        f"test/{tech}/{case}/ord/run.sh": run_sh,
        f"test/{tech}/{case}/ord/eval.sh": eval_sh,
        "designs/asap7_3D/gcd/config.mk":
            "export DESIGN_NAME = gcd\nexport PLATFORM = asap7_3D\n"
            "export SC_LEF_UPPER_COVER = a\n",
        "designs/asap7_3D/gcd/config2d.mk":
            "export DESIGN_NAME = gcd\nexport PLATFORM = asap7\n"
            "export VERILOG_FILES = $(sort $(wildcard $(DESIGN_HOME)/src/$(DESIGN_NAME)/*.v))\n"
            "export SDC_FILE = $(DESIGN_HOME)/asap7_3D/$(DESIGN_NAME)/constraint.sdc\n",
    }
    top_cell = "ibex_core" if case == "ibex" else case
    files[f"designs/{tech}/{case}/config.mk"] = (
        f"export DESIGN_NAME = {top_cell}\nexport PLATFORM = {tech}\n"
    )
    files[f"designs/{tech}/{case}/config2d.mk"] = (
        f"export DESIGN_NAME = {top_cell}\nexport PLATFORM = fixture-2d\n"
    )
    source_commit = commit_repo(source, files)
    orfs = tmp_path / "orfs-research"
    orfs_commit = commit_repo(orfs, {"README.md": "fixed ORFS fixture\n"})
    openroad = orfs / "tools/OpenROAD"
    openroad_commit = commit_repo(openroad, {"README.md": "fixed OpenROAD fixture\n"})
    # The nested repository made the parent dirty; record its gitlink.
    subprocess.run(["git", "-C", str(orfs), "add", "tools/OpenROAD"], check=True)
    subprocess.run(["git", "-C", str(orfs), "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "-qm", "pin openroad"], check=True)
    orfs_commit = subprocess.check_output(["git", "-C", str(orfs), "rev-parse", "HEAD"], text=True).strip()
    bin_dir = orfs / "tools/install/bin"
    bin_dir.mkdir(parents=True)
    for name in ("openroad", "yosys"):
        path = bin_dir / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
    subprocess.run(["git", "-C", str(orfs), "add", "tools/install/bin"], check=True)
    subprocess.run(["git", "-C", str(orfs), "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "-qm", "fixture tools"], check=True)
    orfs_commit = subprocess.check_output(["git", "-C", str(orfs), "rev-parse", "HEAD"], text=True).strip()
    profile = TaiWeiToolchainProfile(orfs, bin_dir / "openroad", bin_dir / "yosys",
                                    orfs_commit=orfs_commit, openroad_commit=openroad_commit)
    return source, source_commit, profile


def test_taiwei_task_defaults_and_generalised_cases():
    task = build_taiwei_task(project_id="p8", timeout_seconds=60)
    assert task.inputs == {"flow": "ord", "tech": "asap7_3D", "case": "gcd"}
    assert task.labels["real_3d_required"] == "true"
    # Generalised: any official case and any 3D platform is accepted.
    ibex = build_taiwei_task(project_id="p8", design_id="ibex",
                             tech="nangate45_3D", timeout_seconds=60)
    assert ibex.inputs == {"flow": "ord", "tech": "nangate45_3D", "case": "ibex"}
    aes = build_taiwei_task(project_id="p8", design_id="aes",
                            tech="asap7_nangate45_3D", timeout_seconds=60)
    assert aes.inputs["case"] == "aes"
    assert aes.inputs["tech"] == "asap7_nangate45_3D"
    linked = build_taiwei_task(
        project_id="p8", registered_design_id="design-07-deadbeef", timeout_seconds=60
    )
    assert linked.design_id == "design-07-deadbeef"
    assert linked.inputs["case"] == "gcd"


def test_taiwei_technology_matrix_is_explicitly_bounded_to_pinned_profiles():
    profiles = taiwei_technology_profiles()
    assert set(profiles) == {"asap7_3D", "nangate45_3D", "asap7_nangate45_3D"}
    assert "arbitrary_pdk" in profiles["asap7_3D"]["not_claimed"]
    assert "thermal_signoff" in profiles["asap7_3D"]["not_claimed"]


def test_taiwei_task_parameters_are_allowlisted_and_typed():
    task = build_taiwei_task(
        project_id="p8", timeout_seconds=60,
        parameters={
            "core_utilization_pct": 55, "num_cores": 16, "cts_layer": "upper",
            "outer_iterations": 2, "skip_2d_part": True,
            "pin3d_allow_net_flow": False, "pin3d_split_net_flow": True,
            "abc_area": True, "start_from": "ord-cts",
        },
    )
    assert task.parameters == {
        "core_utilization_pct": 55, "num_cores": 16, "cts_layer": "upper",
        "outer_iterations": 2, "skip_2d_part": True,
        "pin3d_allow_net_flow": False, "pin3d_split_net_flow": True,
        "abc_area": True, "start_from": "ord-cts",
    }
    with pytest.raises(ValueError, match="core_utilization_pct"):
        build_taiwei_task(project_id="p8", parameters={"core_utilization_pct": 0})
    with pytest.raises(ValueError, match="cts_layer"):
        build_taiwei_task(project_id="p8", parameters={"cts_layer": "side"})
    with pytest.raises(ValueError, match="num_cores"):
        build_taiwei_task(project_id="p8", parameters={"num_cores": 0})
    with pytest.raises(ValueError, match="skip_2d_part must be a boolean"):
        build_taiwei_task(project_id="p8", parameters={"skip_2d_part": "false"})
    with pytest.raises(ValueError, match="abc_area must be a boolean"):
        build_taiwei_task(project_id="p8", parameters={"abc_area": 1})
    with pytest.raises(ValueError, match="clock_period_ns"):
        build_taiwei_task(project_id="p8", clock_period_ns=float("nan"))
    with pytest.raises(ValueError, match="clock_period_ns"):
        build_taiwei_task(project_id="p8", clock_period_ns=0)


def test_taiwei_task_rejects_invalid_platforms():
    with pytest.raises(ValueError, match="3D platform"):
        build_taiwei_task(project_id="p8", tech="nangate45")
    with pytest.raises(ValueError, match="case name"):
        build_taiwei_task(project_id="p8", design_id="bad name")


def test_toolchain_commit_mismatch_fails_closed(tmp_path):
    source, source_commit, profile = fixture_environment(tmp_path)
    wrong = TaiWeiToolchainProfile(profile.orfs_root, profile.openroad_bin,
                                   profile.yosys_bin, orfs_commit="0" * 40,
                                   openroad_commit=profile.openroad_commit)
    with pytest.raises(ValueError, match="commit mismatch"):
        taiwei_plugin_manifest(source, wrong, expected_commit=source_commit)


def test_black_box_adapter_stages_source_and_registers_3d_artifacts(tmp_path):
    source, source_commit, profile = fixture_environment(tmp_path)
    manifest = taiwei_plugin_manifest(source, profile, python_executable=sys.executable,
                                      expected_commit=source_commit,
                                      default_timeout_seconds=30)
    runtime = WorkflowRuntime(RuntimeStore(tmp_path / "runtime.db"),
                              PluginRegistry([manifest]),
                              workspace_root=tmp_path / "runs", worker_id="p8-fixture")
    run = runtime.submit(build_taiwei_task(project_id="p8", timeout_seconds=30))
    completed = runtime.execute_once(run.run_id)
    view = runtime.describe(run.run_id)
    attempt = view["stages"][0]["attempts"][0]
    assert completed.status is RuntimeStatus.SUCCEEDED
    assert {item["kind"] for item in attempt["artifacts"]} == {
        "three_d_eval", "three_d_summary", "gds", "def", "odb", "netlist", "three_d_view",
        "toolchain_snapshot", "log",
    }
    assert not (source / "reports").exists()
    snapshot = next(item for item in attempt["artifacts"]
                    if item["kind"] == "toolchain_snapshot")
    payload = json.loads((Path(attempt["workspace"]) / snapshot["store_key"]).read_text())
    assert payload["orfs_commit"] == profile.orfs_commit


def test_black_box_adapter_generalised_case_and_parameters(tmp_path):
    source, source_commit, profile = fixture_environment(tmp_path, case="ibex",
                                                         tech="nangate45_3D")
    manifest = taiwei_plugin_manifest(source, profile, python_executable=sys.executable,
                                      expected_commit=source_commit,
                                      default_timeout_seconds=30)
    runtime = WorkflowRuntime(RuntimeStore(tmp_path / "runtime.db"),
                              PluginRegistry([manifest]),
                              workspace_root=tmp_path / "runs", worker_id="p8-fixture")
    run = runtime.submit(build_taiwei_task(
        project_id="p8", design_id="ibex", tech="nangate45_3D", timeout_seconds=30,
        parameters={
            "core_utilization_pct": 55, "num_cores": 4, "cts_layer": "upper",
            "outer_iterations": 2, "skip_2d_part": True,
            "pin3d_allow_net_flow": False, "pin3d_split_net_flow": True,
            "abc_area": True, "start_from": "ord-cts",
        },
    ))
    completed = runtime.execute_once(run.run_id)
    view = runtime.describe(run.run_id)
    attempt = view["stages"][0]["attempts"][0]
    assert completed.status is RuntimeStatus.SUCCEEDED
    snapshot = next(item for item in attempt["artifacts"]
                    if item["kind"] == "toolchain_snapshot")
    payload = json.loads((Path(attempt["workspace"]) / snapshot["store_key"]).read_text())
    assert payload["case"] == "ibex"
    assert payload["tech"] == "nangate45_3D"
    assert payload["top_cell"] == "ibex_core"
    assert payload["parameters"]["core_utilization_pct"] == 55
    assert payload["parameters"]["cts_layer"] == "upper"
    assert payload["engine_environment"] == {
        "CORE_UTILIZATION": "55", "NUM_CORES": "4", "CTS_LAYER": "upper",
        "OUTER_ITERATIONS": "2", "SKIP_2D_PART": "1",
        "PIN3D_ALLOW_NET_FLOW": "off", "PIN3D_SPLIT_NET_FLOW": "on",
        "ABC_AREA": "1", "START_FROM": "ord-cts",
    }


def test_dynamic_case_generation_from_platform_rtl(tmp_path):
    """A non-engine case (user RTL) is generated from the gcd template."""
    source, source_commit, profile = fixture_environment(tmp_path)
    # Remove the fixture's dispatch script for the non-official case mux4.
    # The fixture run_experiments.py asserts flow/tech/case values, so use a
    # dedicated engine script that also validates the generated config files.
    script = '''#!/usr/bin/env python3
import argparse,json,sys
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument("--flow"); p.add_argument("--tech"); p.add_argument("--case"); p.add_argument("--run-only",action="store_true"); p.add_argument("--status-interval"); a=p.parse_args()
assert (a.flow,a.tech,a.case)==("ord","asap7_3D","mux4"), (a.flow,a.tech,a.case)
base=Path(".")
# Generated design config must exist and carry the requested case.
mk=(base/"designs/asap7_3D/mux4/config.mk").read_text()
assert "DESIGN_NAME = mux4" in mk, mk
mk2d=(base/"designs/asap7_3D/mux4/config2d.mk").read_text()
assert "DESIGN_NAME = mux4" in mk2d, mk2d
sdc=(base/"designs/asap7_3D/mux4/constraint.sdc").read_text()
assert "clk_port_name clk" in sdc and "set clk_period 8.0" in sdc, sdc
rtls=list((base/"designs/src/mux4").glob("*.v"))
assert rtls and "module mux4" in rtls[0].read_text(), rtls
assert (base/"test/asap7_3D/mux4/ord/run.sh").is_file()
for name,data in (("reports/openroad_eval.json",json.dumps({"wns_ns":0.1})),("logs/final_summary.txt","ok\\n"),("results/final.gds","GDSII"),("results/6_final.def","DEF"),("results/6_final.odb","ODB"),("results/6_final.v","module mux4; endmodule\\n"),("reports/final_3d.png","PNG")):
 path=Path(name); path.parent.mkdir(parents=True,exist_ok=True); path.write_text(data)
'''
    (source / "run_experiments.py").write_text(script, encoding="utf-8")
    (source / "run_experiments.py").chmod(0o755)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "-aqm", "dynamic fixture"],
                   check=True)
    source_commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    # The gcd template tree and common wrappers already ship in the fixture;
    # the dynamic case (mux4) has no run.sh, which is what triggers generation.

    rtl_file = tmp_path / "mux4.v"
    rtl_file.write_text("module mux4(input clk); endmodule\n", encoding="utf-8")
    manifest = taiwei_plugin_manifest(source, profile, python_executable=sys.executable,
                                      expected_commit=source_commit,
                                      default_timeout_seconds=30)
    runtime = WorkflowRuntime(RuntimeStore(tmp_path / "runtime.db"),
                              PluginRegistry([manifest]),
                              workspace_root=tmp_path / "runs", worker_id="p8-fixture")
    run = runtime.submit(build_taiwei_task(
        project_id="p8", design_id="mux4", tech="asap7_3D", timeout_seconds=30,
        rtl={"path": str(rtl_file), "size_bytes": rtl_file.stat().st_size,
             "sha256": "0" * 64},
        clock="clk", clock_period_ns=8.0,
        parameters={"core_utilization_pct": 45},
    ))
    completed = runtime.execute_once(run.run_id)
    view = runtime.describe(run.run_id)
    attempt = view["stages"][0]["attempts"][0]
    assert completed.status is RuntimeStatus.SUCCEEDED, attempt.get("failure")
    snapshot = next(item for item in attempt["artifacts"]
                    if item["kind"] == "toolchain_snapshot")
    payload = json.loads((Path(attempt["workspace"]) / snapshot["store_key"]).read_text())
    assert payload["case"] == "mux4"
    assert payload["parameters"]["core_utilization_pct"] == 45
