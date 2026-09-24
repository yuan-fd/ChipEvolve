"""Run every application smoke as part of the default quality suite."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_SMOKES = [
    "dse_lab",
    "evidence_console",
    "plan_executor",
    "query_agent",
    "run_console",
]


@pytest.mark.parametrize("app", APP_SMOKES)
def test_application_smoke_runs_as_a_real_process(app: str) -> None:
    smoke = REPO_ROOT / "apps" / app / "smoke.py"
    completed = subprocess.run(
        [sys.executable, str(smoke)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, (
        f"{app} smoke failed with exit code {completed.returncode}:\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
