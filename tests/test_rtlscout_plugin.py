from __future__ import annotations

import hashlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from openroad_platform_contracts import RuntimeStatus
from openroad_platform_execution import (
    PluginRegistry,
    ToolchainConfig,
    build_rtlscout_task,
    orfs_plugin_manifest,
    rtlscout_plugin_manifest,
)
from openroad_platform_scheduler import (
    RuntimeStore,
    WorkflowRuntime,
    execute_rtl_to_orfs,
)


FIXTURES = Path(__file__).parent / "fixtures"
REPOSITORY = Path(__file__).parents[1]


def _executable(path: Path, text: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def fake_source(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "rtlscout"
    source.mkdir()
    shutil.copy2(FIXTURES / "fake_rtlscout_cli.py", source / "run_benchmark.py")
    (source / "deps/spire-hdl").mkdir(parents=True)
    (source / "deps/spire-hdl/README.md").write_text("pinned fixture\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run([
        "git", "-C", str(source), "-c", "user.name=Test",
        "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture",
    ], check=True)
    commit = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"], check=True,
        stdout=subprocess.PIPE, text=True,
    ).stdout.strip()
    return source, commit


def manifest(tmp_path: Path):
    source, commit = fake_source(tmp_path)
    verilator = _executable(tmp_path / "bin/verilator")
    yosys = _executable(tmp_path / "bin/yosys")
    return rtlscout_plugin_manifest(
        source, sys.executable, verilator_bin=verilator, yosys_bin=yosys,
        expected_commit=commit, default_timeout_seconds=30,
    )


def test_rtlscout_task_rejects_secret_and_invalid_provider():
    task = build_rtlscout_task(
        project_id="p4", design_id="adder", benchmark="simple_adder",
        model="fake:simple_adder_pass", max_steps=3,
    )
    serialized = json.dumps(task.to_dict())
    assert "API_KEY" not in serialized
    assert task.resources["credential_env"] is None
    codex = build_rtlscout_task(
        project_id="p4", design_id="adder", benchmark="simple_adder",
        model="codex-cli:gpt-5.6-terra", max_steps=3,
    )
    assert codex.parameters["provider"] == "codex-cli"
    assert codex.resources["credential_env"] is None
    with pytest.raises(ValueError, match="Unsupported"):
        build_rtlscout_task(
            project_id="p4", design_id="adder", benchmark="simple_adder",
            model="unknown:model",
        )


def test_rtlscout_adapter_registers_validated_rtl_and_metrics(tmp_path):
    plugin = manifest(tmp_path)
    task = build_rtlscout_task(
        project_id="p4", design_id="adder", benchmark="simple_adder",
        model="fake:simple_adder_pass", max_steps=3, timeout_seconds=30,
    )
    store = RuntimeStore(tmp_path / "runtime.db")
    runtime = WorkflowRuntime(
        store, PluginRegistry([plugin]), workspace_root=tmp_path / "attempts",
        worker_id="p4-test",
    )
    run = runtime.submit(task, capability="agent.rtl.generate")
    completed = runtime.execute_once(run.run_id)
    attempt = runtime.describe(run.run_id)["stages"][0]["attempts"][0]

    assert completed.status is RuntimeStatus.SUCCEEDED
    assert {item["kind"] for item in attempt["artifacts"]} == {
        "rtl", "rtlscout_result", "report", "log",
    }
    rtl = next(item for item in attempt["artifacts"] if item["kind"] == "rtl")
    path = Path(attempt["workspace"]) / rtl["store_key"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == rtl["sha256"]
    assert {item["name"] for item in attempt["metrics"]} == {
        "rtlscout.transistors", "rtlscout.num_cells",
    }


def test_rtlscout_real_provider_without_injected_credential_fails_closed(tmp_path):
    plugin = manifest(tmp_path)
    task = build_rtlscout_task(
        project_id="p4", design_id="adder", benchmark="simple_adder",
        model="anthropic:example", max_steps=3, timeout_seconds=30,
    )
    store = RuntimeStore(tmp_path / "runtime.db")
    runtime = WorkflowRuntime(
        store, PluginRegistry([plugin]), workspace_root=tmp_path / "attempts",
    )
    run = runtime.submit(task)
    completed = runtime.execute_once(run.run_id)
    attempt = runtime.describe(run.run_id)["stages"][0]["attempts"][0]
    assert completed.status is RuntimeStatus.FAILED
    assert attempt["failure"]["category"] == "credential_unavailable"


def fake_orfs_toolchain(tmp_path: Path) -> ToolchainConfig:
    orfs = tmp_path / "orfs"
    flow = orfs / "flow"
    (flow / "platforms/nangate45").mkdir(parents=True)
    (flow / "platforms/nangate45/config.mk").write_text(
        "export PLATFORM = nangate45\n", encoding="utf-8"
    )
    (flow / "Makefile").write_text(
        "OUT := $(WORK_HOME)/results/nangate45/generated_top/base\n"
        "define emit\n\n\tmkdir -p $(OUT)\n\tprintf odb > $(OUT)/$(1)\nendef\n"
        "synth:\n\t$(call emit,1_synth.odb)\n"
        "floorplan:\n\t$(call emit,2_floorplan.odb)\n"
        "place:\n\t$(call emit,3_place.odb)\n"
        "cts:\n\t$(call emit,4_cts.odb)\n"
        "route:\n\t$(call emit,5_route.odb)\n"
        "finish:\n\t$(call emit,6_final.odb)\n"
        "\tprintf def > $(OUT)/6_final.def\n"
        "\tprintf netlist > $(OUT)/6_final.v\n"
        "\tprintf gds > $(OUT)/6_final.gds\n",
        encoding="utf-8",
    )
    openroad = _executable(tmp_path / "orfs-bin/openroad", "#!/bin/sh\nprintf 'openroad fake\\n'\n")
    yosys = _executable(tmp_path / "orfs-bin/yosys", "#!/bin/sh\nprintf 'yosys fake\\n'\n")
    return ToolchainConfig(
        name="p4-fake", orfs_root=orfs, openroad_bin=openroad,
        yosys_bin=yosys, klayout_bin=None,
    )


def test_rtlscout_to_orfs_composition_preserves_source_hash(tmp_path):
    rtl_plugin = manifest(tmp_path)
    orfs_plugin = orfs_plugin_manifest(fake_orfs_toolchain(tmp_path))
    runtime = WorkflowRuntime(
        RuntimeStore(tmp_path / "runtime.db"),
        PluginRegistry([rtl_plugin, orfs_plugin]),
        workspace_root=tmp_path / "attempts", worker_id="p4-composite",
    )
    task = build_rtlscout_task(
        project_id="p4", design_id="generated", benchmark="simple_adder",
        model="fake:simple_adder_pass", max_steps=3, timeout_seconds=30,
    )
    result = execute_rtl_to_orfs(
        runtime, task, top="generated_top",
        orfs_options={"timeout_seconds": 30, "stage_timeout_seconds": 10},
    )

    assert result.status is RuntimeStatus.SUCCEEDED
    assert result.orfs_run_id
    orfs_view = runtime.describe(result.orfs_run_id)
    spec = orfs_view["run"]["task_spec"]
    assert spec["inputs"]["rtl"]["sha256"] == result.rtl_artifact_sha256
    assert spec["labels"]["source_run_id"] == result.rtl_run_id
    kinds = {
        item["kind"]
        for item in orfs_view["stages"][0]["attempts"][0]["artifacts"]
    }
    assert {"gds", "def", "netlist", "run_result"} <= kinds


def test_repository_rtlscout_manifest_is_arch_compatible():
    registry = PluginRegistry.from_directory(REPOSITORY / "integrations/rtlscout")
    loaded = registry.resolve(
        "rtlscout", version="1.0.0", capability="agent.rtl.generate",
        arch=platform.machine(),
    )
    assert Path(loaded.adapter_entry[1]).is_file()
