from __future__ import annotations

from pathlib import Path

import pytest

from apps.api.app import ApiState


ROOT = Path(__file__).resolve().parents[1]


def test_frontend_exposes_readable_code_and_evidence_dashboard() -> None:
    html = (ROOT / "apps/web/index.html").read_text(encoding="utf-8")
    css = (ROOT / "apps/web/assets/app.css").read_text(encoding="utf-8")
    javascript = (ROOT / "apps/web/assets/app.js").read_text(encoding="utf-8")

    assert "RTLScout run dashboard" in html
    assert "How RTLScout works" in html
    assert "These are separate tools, not additional RTLScout steps" in html
    assert ".code-viewer" in css and "background: #fff" in css
    assert ".provider-connect" in css and "background: #111827" in css
    assert "formatCodeForDisplay" in javascript
    assert "result.all_evals" in javascript
    assert "pendingExtension" in javascript


def test_rtlscout_web_task_is_bounded_and_contains_no_credential(tmp_path: Path) -> None:
    state = ApiState(
        tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
        design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
        yosys_bin=ROOT.parent / "bin/yosys",
        runtime_db_path=tmp_path / "runtime.db",
        campaign_db_path=tmp_path / "campaign.db",
    )
    status = state.rtlscout_status()
    assert status["ready"] is True
    assert status["offline_demo"]["benchmarks"] == ["simple_adder"]
    assert status["offline_demo"]["api_key_required"] is False

    submitted = state.submit_rtlscout({
        "mode": "offline_demo", "benchmark": "simple_adder",
        "cost_metric": "yosys_cells", "max_steps": 3,
        "api_key": "must-not-enter-task",
    })
    task = submitted["run"]["run"]["task_spec"]
    assert task["parameters"]["model"] == "fake:simple_adder_pass"
    assert task["parameters"]["max_steps"] == 3
    assert task["parameters"]["cost_metric"] == "yosys_cells"
    assert "must-not-enter-task" not in str(task)

    with pytest.raises(ValueError, match="secure worker secret bridge"):
        state.submit_rtlscout({"mode": "byok"})
    with pytest.raises(ValueError, match="simple_adder"):
        state.submit_rtlscout({"benchmark": "unbounded-benchmark"})
