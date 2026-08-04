#!/usr/bin/env python3
"""Run and seal one real TaiWei gcd acceptance through Workflow Runtime."""

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
               ROOT / "packages/scheduler/src"):
    sys.path.insert(0, str(source))

from openroad_platform_contracts import RuntimeStatus  # noqa: E402
from openroad_platform_execution import (  # noqa: E402
    PluginRegistry,
    TaiWeiToolchainProfile,
    build_taiwei_task,
    taiwei_plugin_manifest,
)
from openroad_platform_scheduler import RuntimeStore, WorkflowRuntime  # noqa: E402


REQUIRED_KINDS = {
    "three_d_eval", "three_d_summary", "gds", "def", "odb", "netlist",
    "sdc", "spef", "three_d_report", "three_d_view", "toolchain_snapshot", "log",
}
REQUIRED_METRICS = {
    "finish__design__core__area",
    "finish__design__instance__area__stdcell",
    "finish__power__total",
    "finish__route__wirelength",
    "finish__timing__setup__ws",
    "finish__timing__setup__tns",
    "finish__route__drc_errors",
    "finish__fep__violations",
    "finish__route__hb_via__count__phys",
    "finish__route__cross_tier_nets__all",
    "finish__route__cross_tier_nets__upper_bottom",
    "finish__route__cross_tier_nets__upper_io",
    "finish__route__cross_tier_nets__bottom_io",
    "finish__route__cross_tier_nets__upper_bottom_io",
    "finish__placement__upper_instances",
    "finish__placement__bottom_instances",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def _backup_database(source: Path, destination: Path) -> None:
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as backup_db:
        source_db.backup(backup_db)
    with sqlite3.connect(destination) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise RuntimeError(f"Runtime backup integrity check failed: {result}")


def _verify_lock(lock_path: Path, profile: TaiWeiToolchainProfile,
                 source_root: Path) -> dict:
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    binaries = {
        "openroad": profile.openroad_bin,
        "yosys": profile.yosys_bin,
        "yosys_slang_plugin": profile.yosys_bin.parent.parent
        / "share/yosys/plugins/slang.so",
    }
    binary_results = {}
    for name, path in binaries.items():
        actual = _sha256(path)
        expected = lock["binary_sha256"][name]
        binary_results[name] = {
            "path": str(path), "expected_sha256": expected,
            "actual_sha256": actual, "verified": actual == expected,
        }
        if actual != expected:
            raise RuntimeError(f"Pinned {name} SHA-256 mismatch")
    source_commits = {
        "taiwei_commit": _git(source_root),
        "orfs_research_commit": _git(profile.orfs_root),
        "openroad_commit": _git(profile.orfs_root / "tools/OpenROAD"),
    }
    for name, actual in source_commits.items():
        if actual != lock["source"][name]:
            raise RuntimeError(f"Pinned source mismatch: {name}")
    license_hashes = {
        "taiwei": _sha256(source_root / "LICENSE"),
        "orfs_research_build_run_scripts": _git_blob_sha256(
            profile.orfs_root, "LICENSE_BUILD_RUN_SCRIPTS"
        ),
        "openroad": _sha256(profile.orfs_root / "tools/OpenROAD/LICENSE"),
        "yosys": _sha256(profile.orfs_root / "tools/yosys/COPYING"),
    }
    for name, actual in license_hashes.items():
        if actual != lock["licenses"][name]["sha256"]:
            raise RuntimeError(f"Pinned license mismatch: {name}")
    return {
        "lock_path": str(lock_path.relative_to(ROOT)),
        "lock_sha256": _sha256(lock_path),
        "architecture": lock["architecture"],
        "source_commits": source_commits,
        "binaries": binary_results,
        "licenses": license_hashes,
        "asap7_3d_redistribution_allowed": False,
        "verified": True,
    }


def _git(path: Path) -> str:
    import subprocess
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()


def _git_blob_sha256(repository: Path, object_name: str) -> str:
    import subprocess
    content = subprocess.check_output(
        ["git", "-C", str(repository), "show", f"HEAD:{object_name}"]
    )
    return hashlib.sha256(content).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=21600)
    parser.add_argument("--num-cores", type=int, default=8)
    args = parser.parse_args()

    output = args.output_root.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Acceptance output must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    tool_root = ROOT / ".tools/taiwei-official-3d"
    orfs = tool_root / "orfs-research"
    source_root = ROOT / ".external-src/taiwei-pin-3d"
    profile = TaiWeiToolchainProfile(
        orfs_root=orfs,
        openroad_bin=orfs / "tools/install/OpenROAD/bin/openroad",
        yosys_bin=orfs / "tools/install/yosys/bin/yosys",
        runtime_library_paths=(
            tool_root / "dependencies/lib",
            tool_root / "dependencies/lib64",
            Path("/opt/openEuler/gcc-toolset-12/root/usr/lib64"),
        ),
    )
    lock_verification = _verify_lock(
        ROOT / "integrations/taiwei_pin_3d/environment.lock.json", profile, source_root
    )
    _write(output / "toolchain_lock_verification.json", lock_verification)

    manifest = taiwei_plugin_manifest(
        source_root, profile, python_executable=sys.executable,
        default_timeout_seconds=args.timeout_seconds, num_cores=args.num_cores,
    )
    live_root = Path("/tmp") / f"openroad-platform-p8-real-{uuid.uuid4().hex}"
    db_path = live_root / "runtime.db"
    workspace_root = output / "runtime-workspaces"
    runtime = WorkflowRuntime(
        RuntimeStore(db_path), PluginRegistry([manifest]),
        workspace_root=workspace_root, worker_id="p8-real-acceptance", lease_seconds=60,
    )
    task = build_taiwei_task(
        project_id="p8-real-platform-acceptance", design_id="gcd",
        timeout_seconds=args.timeout_seconds, task_id="p8-real-taiwei-gcd",
    )
    submitted = runtime.submit(task)
    completed = runtime.execute_once(submitted.run_id)
    description = runtime.describe(submitted.run_id)
    _write(output / "runtime_run_snapshot.json", description)
    _write(output / "runtime_event_chain.json", description["events"])

    attempts = description["stages"][0]["attempts"]
    if completed.status is not RuntimeStatus.SUCCEEDED or len(attempts) != 1:
        failure = attempts[-1]["failure"] if attempts else None
        raise RuntimeError(f"Real Runtime run did not succeed: {completed.status.value}: {failure}")
    attempt = attempts[0]
    workspace = Path(attempt["workspace"])
    artifacts = attempt["artifacts"]
    kinds = {item["kind"] for item in artifacts}
    if missing := sorted(REQUIRED_KINDS - kinds):
        raise RuntimeError(f"Required Runtime artifact kinds missing: {missing}")

    inventory = []
    for artifact in artifacts:
        path = (workspace / artifact["store_key"]).resolve()
        path.relative_to(workspace.resolve())
        actual = _sha256(path)
        if actual != artifact["sha256"]:
            raise RuntimeError(f"Artifact SHA-256 mismatch: {artifact['store_key']}")
        inventory.append({**artifact, "absolute_path": str(path),
                          "sha256_recomputed": actual, "verified": True})
    _write(output / "artifact_inventory.json", inventory)

    gds_items = [item for item in inventory if item["kind"] == "gds"]
    if len(gds_items) != 1 or not gds_items[0]["store_key"].endswith("/6_final.gds"):
        raise RuntimeError(f"Expected exactly one final gcd GDS, found {gds_items}")
    gds_path = Path(gds_items[0]["absolute_path"])
    if gds_path.read_bytes()[:4] != b"\x00\x06\x00\x02":
        raise RuntimeError("Final artifact is not a GDSII stream")

    provenance_items = [item for item in inventory
                        if item["store_key"].endswith("streamout_provenance.json")]
    if len(provenance_items) != 1:
        raise RuntimeError("Unique stream-out provenance artifact is required")
    provenance = json.loads(
        Path(provenance_items[0]["absolute_path"]).read_text(encoding="utf-8")
    )
    via_geometry = provenance.get("custom_via_geometry", {})
    if set(via_geometry) != {"VIA_M1m_M2add", "VIA_M2add_M3add"} or not all(
        record.get("verified") for record in via_geometry.values()
    ):
        raise RuntimeError("Custom 3D via stream-out evidence is incomplete")

    metric_names = {item["name"] for item in attempt["metrics"]}
    if missing := sorted(REQUIRED_METRICS - metric_names):
        raise RuntimeError(f"Required Runtime metrics missing: {missing}")
    _write(output / "runtime_metrics.json", attempt["metrics"])

    before_restart = len(attempts)
    restarted = WorkflowRuntime(
        RuntimeStore(db_path), PluginRegistry([manifest]),
        workspace_root=workspace_root, worker_id="p8-real-restarted", lease_seconds=60,
    )
    after = restarted.execute_once(submitted.run_id)
    after_description = restarted.describe(submitted.run_id)
    after_restart = len(after_description["stages"][0]["attempts"])
    restart_safe = (after.status is RuntimeStatus.SUCCEEDED
                    and before_restart == after_restart == 1)
    if not restart_safe:
        raise RuntimeError("Runtime restart created a duplicate attempt")

    db_snapshot = output / "runtime.db.snapshot"
    _backup_database(db_path, db_snapshot)
    database = {
        "live_path": str(db_path), "live_path_under_tmp": str(db_path).startswith("/tmp/"),
        "snapshot_path": str(db_snapshot), "snapshot_sha256": _sha256(db_snapshot),
        "integrity_check": "ok", "restart_safe": restart_safe,
    }
    _write(output / "runtime_database.json", database)

    summary = {
        "schema_version": 1,
        "phase": "P8-Real",
        "accepted": True,
        "run_id": submitted.run_id,
        "status": completed.status.value,
        "project_id": task.project_id,
        "design_id": task.design_id,
        "plugin": f"{manifest.plugin_id}@{manifest.plugin_version}",
        "toolchain_lock_verified": True,
        "runtime_is_state_authority": True,
        "runtime_db_under_tmp": database["live_path_under_tmp"],
        "restart_safe": restart_safe,
        "artifact_count": len(inventory),
        "artifact_kinds": sorted(kinds),
        "metric_count": len(attempt["metrics"]),
        "gds": {"path": gds_items[0]["store_key"], "size_bytes": gds_items[0]["size_bytes"],
                "sha256": gds_items[0]["sha256"], "gdsii_header_verified": True},
        "custom_via_geometry": via_geometry,
        "evidence_files": [
            "toolchain_lock_verification.json", "runtime_run_snapshot.json",
            "runtime_event_chain.json", "artifact_inventory.json", "runtime_metrics.json",
            "runtime_database.json", "runtime.db.snapshot",
        ],
    }
    _write(output / "acceptance_summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
