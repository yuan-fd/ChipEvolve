from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from apps.api.app import ApiState


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_worker_once_advances_the_workflow_runtime_queue(tmp_path: Path) -> None:
    state = ApiState(
        tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
        design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
        yosys_bin=ROOT.parent / "bin" / "yosys",
        runtime_db_path=tmp_path / "runtime.db",
        optimization_db_path=tmp_path / "optimization.db",
    )
    submitted = state.submit_edacraft_smoke("edacode")
    run_id = submitted["run"]["run"]["run_id"]

    completed = subprocess.run([
        sys.executable, str(ROOT / "scripts/run_runtime_worker.py"), "--once",
        "--db", str(tmp_path / "platform.db"),
        "--upload-root", str(tmp_path / "uploads"),
        "--design-root", str(tmp_path / "designs"),
        "--legacy-root", str(tmp_path / "legacy"),
        "--runtime-db", str(tmp_path / "runtime.db"),
        "--optimization-db", str(tmp_path / "optimization.db"),
        "--orfs-root", str(tmp_path / "orfs"),
        "--heartbeat", str(tmp_path / "runtime-worker.heartbeat.json"),
    ], cwd=ROOT, text=True, capture_output=True, timeout=60, check=False)

    assert completed.returncode == 0, completed.stderr
    assert state.runtime_store.get_run(run_id).status.value == "succeeded"
