"""TaiWei 3D end-to-end acceptance test.

Runs the real 20-stage 3D physical-design flow (RTL -> 3D GDS) through the
WorkflowRuntime exactly as production does, then asserts:

- the run reaches ``succeeded`` (not failed / cancelled / stuck),
- every required artifact kind is present (three_d_eval, three_d_summary,
  gds, def, odb, netlist, toolchain_snapshot, log),
- metrics were parsed and recorded,
- the toolchain snapshot is pinned to the expected commits and marked real_3d.

The test is skipped automatically when the pinned TaiWei toolchain is not
installed (CI machines), so it is safe to keep in the normal suite.
Run it explicitly on the real server with::

    python3 -m pytest tests/test_taiwei_3d_e2e.py -v -m real_3d --no-header

A full gcd 3D flow takes roughly 30-60 minutes on the reference server.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "packages/contracts/src", ROOT / "packages/execution/src",
               ROOT / "packages/scheduler/src"):
    sys.path.insert(0, str(source))

from openroad_platform_contracts import RuntimeStatus  # noqa: E402
from openroad_platform_execution import (  # noqa: E402
    PluginRegistry, TaiWeiToolchainProfile, build_taiwei_task,
    taiwei_plugin_manifest,
)
from openroad_platform_scheduler import RuntimeStore, WorkflowRuntime  # noqa: E402

TOOL_ROOT = ROOT / ".tools" / "taiwei-official-3d"
TAIWEI_SOURCE = ROOT / ".external-src" / "taiwei-pin-3d"

_REQUIRED_ARTIFACTS = {"three_d_eval", "three_d_summary", "gds", "def", "odb",
                       "netlist", "toolchain_snapshot", "log"}

TERMINAL = {RuntimeStatus.SUCCEEDED, RuntimeStatus.FAILED, RuntimeStatus.CANCELLED}


def _toolchain_available() -> bool:
    return all((
        TAIWEI_SOURCE.joinpath("run_experiments.py").is_file(),
        TOOL_ROOT.joinpath("orfs-research").is_dir(),
        TOOL_ROOT.joinpath("openroad-build-gcc12", "bin", "openroad").is_file(),
        TOOL_ROOT.joinpath("orfs-research", "tools", "install", "yosys", "bin",
                           "yosys").is_file(),
    ))


pytestmark = pytest.mark.skipif(
    not _toolchain_available(),
    reason="TaiWei 3D toolchain not installed; run scripts/build_taiwei_official_toolchain.sh")


def _profile() -> TaiWeiToolchainProfile:
    return TaiWeiToolchainProfile(
        orfs_root=TOOL_ROOT / "orfs-research",
        openroad_bin=TOOL_ROOT / "openroad-build-gcc12" / "bin" / "openroad",
        yosys_bin=TOOL_ROOT / "orfs-research" / "tools" / "install" / "yosys" / "bin" / "yosys",
        runtime_library_paths=(
            TOOL_ROOT / "dependencies" / "lib",
            TOOL_ROOT / "dependencies" / "lib64",
            Path("/opt/openEuler/gcc-toolset-12/root/usr/lib64"),
        ),
    )


def _run_to_terminal(runtime: WorkflowRuntime, run_id: str):
    """Pump execute_once until the run reaches a terminal state."""
    run = runtime.store.get_run(run_id)
    while run.status not in TERMINAL:
        run = runtime.execute_once(run_id)
    return run


def _collect_artifacts(runtime: WorkflowRuntime, run_id: str) -> tuple[list, list, list]:
    """Collect artifacts, metrics and workspaces across all stages/attempts."""
    artifacts, metrics, workspaces = [], [], []
    for stage in runtime.store.list_stages(run_id):
        for attempt in runtime.store.list_attempts(stage.stage_run_id):
            workspaces.append(attempt.workspace)
            artifacts.extend(runtime.store.artifacts(attempt.attempt_id))
            metrics.extend(runtime.store.metrics(attempt.attempt_id))
    return artifacts, metrics, workspaces


@pytest.mark.real_3d
@pytest.mark.parametrize("case,tech,parameters", [
    pytest.param("gcd", "asap7_3D", {"core_utilization_pct": 45, "num_cores": 8},
                 id="gcd-asap7_3D"),
])
def test_taiwei_3d_end_to_end(tmp_path, case: str, tech: str, parameters: dict) -> None:
    profile = _profile()
    runtime = WorkflowRuntime(
        RuntimeStore(tmp_path / "runtime.db"),
        PluginRegistry([taiwei_plugin_manifest(TAIWEI_SOURCE, profile)]),
        workspace_root=tmp_path / "workspaces",
        worker_id="e2e-taiwei-3d",
    )
    task = build_taiwei_task(
        project_id="e2e-3d", design_id=case, tech=tech,
        parameters=parameters, timeout_seconds=21600,
    )
    run = runtime.submit(task)
    run = _run_to_terminal(runtime, run.run_id)

    assert run.status is RuntimeStatus.SUCCEEDED, (
        f"TaiWei 3D flow did not succeed: {run.status} "
        f"terminal_reason={run.terminal_reason}")

    artifacts, metrics, workspaces = _collect_artifacts(runtime, run.run_id)
    kinds = {item["kind"] for item in artifacts}
    missing = _REQUIRED_ARTIFACTS - kinds
    assert not missing, f"Required TaiWei artifacts missing: {sorted(missing)}"

    assert metrics, "TaiWei 3D flow produced no metrics"
    # A real 3D flow must at least record cross-tier nets or area/DRC evidence.
    payload = str(metrics)
    assert any(token in payload for token in ("cross_tier", "drc", "area", "wns", "power")), (
        f"Metrics look empty/unspecific: {payload[:300]}")

    snapshots = [a for a in artifacts if a["kind"] == "toolchain_snapshot"]
    assert snapshots, "toolchain_snapshot artifact missing"
    import json
    # store_key is relative to the attempt workspace
    snap_path = None
    for workspace in workspaces:
        candidate = Path(workspace) / snapshots[0]["store_key"]
        if candidate.is_file():
            snap_path = candidate
            break
    assert snap_path is not None, (
        f"toolchain_snapshot not readable under any workspace: "
        f"{snapshots[0].get('store_key')!r} (workspaces={workspaces})")
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    assert snap.get("real_3d") is True
    assert snap.get("case") == case and snap.get("tech") == tech
    assert snap.get("openroad_sha256") and snap.get("yosys_sha256")
