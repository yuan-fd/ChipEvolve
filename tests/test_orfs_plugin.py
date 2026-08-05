from __future__ import annotations

import hashlib
import json
import platform
import threading
import time
from pathlib import Path

from openroad_platform_contracts import RuntimeStatus
from openroad_platform_execution import (
    PluginRegistry,
    ToolchainConfig,
    build_orfs_task,
    orfs_plugin_manifest,
)
from openroad_platform_scheduler import RuntimeStore, WorkflowRuntime


FIXTURES = Path(__file__).parent / "fixtures"
REPOSITORY = Path(__file__).parents[1]


def fake_toolchain(tmp_path: Path) -> ToolchainConfig:
    orfs = tmp_path / "orfs"
    flow = orfs / "flow"
    flow.mkdir(parents=True)
    (flow / "platforms/nangate45").mkdir(parents=True)
    (flow / "platforms/nangate45/config.mk").write_text(
        "export PLATFORM = nangate45\n", encoding="utf-8"
    )
    (flow / "Makefile").write_text(
        "OUT := $(WORK_HOME)/results/nangate45/mux_2to1/base\n"
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
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    for name in ("openroad", "yosys"):
        path = binary_dir / name
        path.write_text("#!/bin/sh\nprintf 'fake-tool 1.0\\n'\n", encoding="utf-8")
        path.chmod(0o755)
    return ToolchainConfig(
        name="fake", orfs_root=orfs,
        openroad_bin=binary_dir / "openroad",
        yosys_bin=binary_dir / "yosys",
        klayout_bin=None,
    )


def test_orfs_plugin_runs_full_runtime_chain_and_records_provenance(tmp_path):
    toolchain = fake_toolchain(tmp_path)
    manifest = orfs_plugin_manifest(toolchain)
    task = build_orfs_task(
        FIXTURES / "p2_mux_2to1.v",
        project_id="p2-test", design_id="mux-test", top="mux_2to1",
        timeout_seconds=30, stage_timeout_seconds=10,
    )
    store = RuntimeStore(tmp_path / "runtime.db")
    runtime = WorkflowRuntime(
        store, PluginRegistry([manifest]),
        workspace_root=tmp_path / "attempts", worker_id="p2-test-worker",
    )

    run = runtime.submit(task, capability="eda.rtl_to_gds")
    completed = runtime.execute_once(run.run_id)
    view = runtime.describe(run.run_id)

    assert completed.status is RuntimeStatus.SUCCEEDED
    attempt = view["stages"][0]["attempts"][0]
    kinds = {item["kind"] for item in attempt["artifacts"]}
    assert {"gds", "def", "netlist", "odb", "config",
            "toolchain_snapshot", "run_result"} <= kinds
    for artifact in attempt["artifacts"]:
        path = Path(attempt["workspace"]) / artifact["store_key"]
        assert path.is_file() and path.stat().st_size == artifact["size_bytes"]
        assert sha256(path) == artifact["sha256"]
    snapshot_artifact = next(
        item for item in attempt["artifacts"] if item["kind"] == "toolchain_snapshot"
    )
    snapshot = json.loads(
        (Path(attempt["workspace"]) / snapshot_artifact["store_key"]).read_text()
    )
    assert snapshot["schema_version"] == 1
    assert snapshot["toolchain"]["name"] == "fake"
    assert snapshot["files"]["rtl"]["sha256"] == task.inputs["rtl"]["sha256"]
    assert snapshot["files"]["generated_config"]["sha256"]
    assert snapshot["files"]["platform_config"]["sha256"]
    tool_events = [event for event in view["events"]
                   if event["event_type"].startswith("tool.stage.")]
    assert [event["payload"]["tool_stage"] for event in tool_events[::2]] == [
        "synth", "floorplan", "place", "cts", "route", "finish"
    ]
    assert all(event["producer"] == "adapter:orfs@1.0.0" for event in tool_events)
    assert [event["event_type"] for event in view["events"]][-1] == "run.finished"


def test_orfs_plugin_rejects_rtl_changed_after_task_creation(tmp_path):
    source = tmp_path / "design.v"
    source.write_text("module top; endmodule\n", encoding="utf-8")
    task = build_orfs_task(
        source, project_id="p2-test", design_id="tamper", top="top",
        timeout_seconds=30, stage_timeout_seconds=10,
    )
    source.write_text("module changed; endmodule\n", encoding="utf-8")
    toolchain = fake_toolchain(tmp_path)
    store = RuntimeStore(tmp_path / "runtime.db")
    runtime = WorkflowRuntime(
        store, PluginRegistry([orfs_plugin_manifest(toolchain)]),
        workspace_root=tmp_path / "attempts", worker_id="p2-test-worker",
    )

    run = runtime.submit(task)
    completed = runtime.execute_once(run.run_id)
    attempt = runtime.describe(run.run_id)["stages"][0]["attempts"][0]

    assert completed.status is RuntimeStatus.FAILED
    assert attempt["failure"]["category"] == "adapter_error"
    assert "SHA-256" in attempt["failure"]["message"]
    assert not (Path(attempt["workspace"]) / "orfs/implementation").exists()


def test_repository_orfs_manifest_is_strict_and_arch_compatible():
    registry = PluginRegistry.from_directory(REPOSITORY / "integrations/orfs")
    manifest = registry.resolve(
        "orfs", version="1.0.0", capability="eda.rtl_to_gds",
        arch=platform.machine(),
    )
    assert Path(manifest.adapter_entry[1]).is_file()


def test_orfs_plugin_live_cancel_stops_nested_make_process(tmp_path):
    toolchain = fake_toolchain(tmp_path)
    output = toolchain.flow_home / "Makefile"
    output.write_text(
        "OUT := $(WORK_HOME)/results/nangate45/mux_2to1/base\n"
        "synth:\n\tmkdir -p $(OUT)\n\tsleep 30\n"
        "\tprintf odb > $(OUT)/1_synth.odb\n",
        encoding="utf-8",
    )
    task = build_orfs_task(
        FIXTURES / "p2_mux_2to1.v",
        project_id="p2-test", design_id="cancel", top="mux_2to1",
        target_stage="synth", timeout_seconds=30, stage_timeout_seconds=30,
    )
    store = RuntimeStore(tmp_path / "runtime.db")
    runtime = WorkflowRuntime(
        store, PluginRegistry([orfs_plugin_manifest(toolchain)]),
        workspace_root=tmp_path / "attempts", worker_id="p2-test-worker",
    )
    run = runtime.submit(task)
    worker = threading.Thread(target=runtime.execute_once, args=(run.run_id,))
    worker.start()
    stage = store.list_stages(run.run_id)[0]
    deadline = time.monotonic() + 3
    while not store.list_attempts(stage.stage_run_id):
        if time.monotonic() >= deadline:
            raise AssertionError("ORFS adapter did not start")
        time.sleep(0.01)

    store.request_cancel(run.run_id)
    worker.join(timeout=5)
    view = runtime.describe(run.run_id)

    assert not worker.is_alive()
    assert view["run"]["status"] == "cancelled"
    attempt = view["stages"][0]["attempts"][0]
    assert attempt["status"] == "cancelled"
    assert attempt["failure"]["category"] == "cancelled"
    assert not any(Path(attempt["workspace"]).rglob("1_synth.odb"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()
