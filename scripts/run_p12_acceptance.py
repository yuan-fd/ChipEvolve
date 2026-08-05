#!/usr/bin/env python3
"""Run one real Codex-Terra Spec-to-GDS chain through authoritative Runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "packages/contracts/src", ROOT / "packages/execution/src",
               ROOT / "packages/scheduler/src", ROOT / "packages/analysis/src",
               ROOT / "packages/visualization/src"):
    sys.path.insert(0, str(source))

from openroad_platform_contracts import RuntimeStatus  # noqa: E402
from openroad_platform_execution import (  # noqa: E402
    PluginRegistry, ToolchainConfig, orfs_plugin_manifest,
)
from openroad_platform_scheduler import (  # noqa: E402
    CodexCliSpecProvider, LimitedReActController, RuntimeStore, SpecConversationManager,
    SpecConversationStore, WorkflowRuntime,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-terra")
    args = parser.parse_args()
    output = args.output_root.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(output)
    output.mkdir(parents=True, exist_ok=True)
    private = Path("/tmp") / f"openroad-platform-p12-{uuid.uuid4().hex}"
    spec_store = SpecConversationStore(private / "spec.db")
    provider = CodexCliSpecProvider(model=args.model, timeout_seconds=240)
    manager = SpecConversationManager(spec_store, provider)
    session = manager.create(message=(
        "设计一个纯组合二输入与门：顶层模块必须叫 and2，输入为 a、b，输出为 y，"
        "功能为 y=a&b。目标 Nangate45，运行完整 OpenROAD 流程并生成 GDS。"
    ))
    _write(output / "spec_session.json", session)
    proposal = session["state"]
    if not proposal["ready_for_execution"] or not proposal.get("rtl_source"):
        raise RuntimeError(f"Codex proposal still requires clarification: {proposal}")
    rtl = output / "and2.v"
    rtl.write_text(proposal["rtl_source"] + "\n", encoding="utf-8")
    task = manager.compile(
        session["session_id"], rtl_path=rtl, design_id="p12-and2", confirmed=True,
    )
    toolchain = ToolchainConfig.from_environment(
        name="p12-real", orfs_root=ROOT.parent / "OpenROAD-flow-scripts",
        openroad_bin=ROOT.parent / "bin/openroad", yosys_bin=ROOT.parent / "bin/yosys",
        klayout_bin=ROOT.parent / "bin/klayout",
    )
    runtime = WorkflowRuntime(
        RuntimeStore(private / "runtime.db"),
        PluginRegistry([orfs_plugin_manifest(toolchain)]),
        workspace_root=output / "runtime-workspaces", worker_id="p12-acceptance",
    )
    run = runtime.submit(task, capability="eda.rtl_to_gds")
    spec_store.bind_run(session["session_id"], run.run_id, design_id="p12-and2")
    finished = runtime.execute_once(run.run_id)
    view = runtime.describe(run.run_id)
    _write(output / "runtime_initial_run_snapshot.json", view)
    repair_action = None
    initial_run_id = run.run_id
    if finished.status is not RuntimeStatus.SUCCEEDED:
        failed_attempt = view["stages"][0]["attempts"][-1]
        failure = dict(failed_attempt.get("failure") or {})
        message = str(failure.get("message") or "").lower()
        if "pdn-0185" not in message and "insufficient width" not in message:
            raise RuntimeError(f"P12 real flow failed outside repair policy: {failure}")
        failure["category"] = "pdn_insufficient_area"
        failure["evidence_refs"] = [
            f"runtime:{run.run_id}:attempt:{failed_attempt['attempt_id']}"
        ]
        controller = LimitedReActController(max_repairs=2)
        repair_action = controller.decide(task, failure)
        task = controller.apply(task, repair_action)
        run = runtime.submit(task, capability="eda.rtl_to_gds")
        finished = runtime.execute_once(run.run_id)
        view = runtime.describe(run.run_id)
    _write(output / "runtime_run_snapshot.json", view)
    if finished.status is not RuntimeStatus.SUCCEEDED:
        raise RuntimeError(f"P12 repaired real flow failed: {view['run']}")
    attempt = view["stages"][0]["attempts"][0]
    kinds = {item["kind"] for item in attempt["artifacts"]}
    required = {"gds", "def", "odb", "netlist", "layout_view", "toolchain_snapshot"}
    if not required.issubset(kinds):
        raise RuntimeError(f"P12 artifacts are incomplete: {sorted(required - kinds)}")
    tool_events = [event for event in view["events"]
                   if event["event_type"].startswith("tool.stage.")]
    if len(tool_events) != 12:
        raise RuntimeError(f"Expected 12 ORFS stage events, got {len(tool_events)}")
    inventory = []
    for item in attempt["artifacts"]:
        path = Path(attempt["workspace"]) / item["store_key"]
        actual = _sha(path)
        if actual != item["sha256"]:
            raise RuntimeError(f"Artifact hash mismatch: {path}")
        inventory.append({**item, "verified": True})
    _write(output / "artifact_inventory.json", inventory)
    backup = output / "runtime.db.snapshot"
    with sqlite3.connect(runtime.store.path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    summary = {
        "schema_version": 1, "phase": "P12", "accepted": True,
        "provider": provider.provider_name, "model": provider.model,
        "session_id": session["session_id"], "run_id": run.run_id,
        "initial_run_id": initial_run_id,
        "status": finished.status.value, "explicit_confirmation": True,
        "runtime_authoritative": True, "idempotent_task_id": task.task_id,
        "tool_stage_event_count": len(tool_events),
        "artifact_count": len(inventory), "required_artifacts": sorted(required),
        "rtl_sha256": _sha(rtl), "runtime_db_under_tmp": str(runtime.store.path).startswith("/tmp/"),
        "repair_action": repair_action.to_dict() if repair_action else None,
        "failed_evidence_preserved": repair_action is not None,
    }
    _write(output / "acceptance_summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _write(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
