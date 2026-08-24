import json
import subprocess
import sys
from pathlib import Path

import pytest


def test_fixed_v2_suite_runner_produces_replayable_report(tmp_path):
    tool = Path("/share/home/yuanwenjie/.local/opt/openroad-rtl-tools/bin/iverilog")
    if not tool.is_file():
        pytest.skip("user-space Icarus is unavailable")
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "suite"
    result = subprocess.run(
        [sys.executable, str(root / "scripts/run_v2_frontend_suite.py"),
         "--output", str(output), "--repeats", "1"],
        cwd=root, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / "report.json").read_text())
    assert len(report["records"]) == 4
    assert report["all_compile_pass"] is True
    assert report["all_simulation_pass"] is True
    assert report["all_lint_pass"] is True
    assert report["source"].startswith("platform-authored")
