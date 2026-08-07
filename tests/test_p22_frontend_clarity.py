from __future__ import annotations

import re
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
    assert "TCADCraft" in html and "MoMCraft" in html and "CktCraft" in html
    assert "RTLCraft" not in html and "EDACode" not in html
    assert ".code-viewer" in css and "background: #fff" in css
    assert ".provider-connect" in css and "background: #111827" in css
    assert "formatCodeForDisplay" in javascript
    assert "result.all_evals" in javascript
    assert 'route("backend")' in javascript
    assert "pendingExtension" not in javascript
    assert 'id="page-extensions"' not in html


def test_frontend_and_backend_follow_the_reference_task_sequence() -> None:
    html = (ROOT / "apps/web/index.html").read_text(encoding="utf-8")
    css = (ROOT / "apps/web/assets/app.css").read_text(encoding="utf-8")
    javascript = (ROOT / "apps/web/assets/app.js").read_text(encoding="utf-8")

    assert html.index("Create or upload RTL") < html.index("Choose an audited RTL example")
    assert html.index("Choose an audited RTL example") < html.index("Synthesis results")
    assert html.index("Choose a registered RTL design") < html.index("Configure implementation")
    assert html.index("Configure implementation") < html.index("Run RTL-to-GDS and monitor stages")
    assert html.index("Run RTL-to-GDS and monitor stages") < html.index("Layout, QoR, and implementation evidence")
    assert 'id="flowProgressBar"' in html
    assert 'id="backendDesignChips"' in html
    assert ".task-panel .stage" in css and "grid-template-columns: 18px 95px 1fr 55px" in css
    assert "attempt.metrics" in javascript
    assert "parameter_grid" in javascript
    assert "state.designs[0]" not in javascript
    assert "physicalRuns[0]" not in javascript
    assert "Design\", \"设计" in javascript and "Run\", \"任务" in javascript
    assert "runtime_worker_ready" in javascript


def test_language_switch_has_persisted_real_translations() -> None:
    html = (ROOT / "apps/web/index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "apps/web/assets/app.js").read_text(encoding="utf-8")

    assert 'data-locale="zh"' in html and 'data-locale="en"' in html
    assert 'data-i18n="backend.run.action"' in html
    assert '"backend.run.action": "开始 RTL-to-GDS"' in javascript
    assert 'localStorage.setItem("openroad-platform-locale"' in javascript
    assert 'document.documentElement.lang = state.locale === "zh" ? "zh-CN" : "en"' in javascript
    html_keys = set(re.findall(r'data-i18n="([^"]+)"', html))
    zh_block = javascript.split("const ZH = {", 1)[1].split("};", 1)[0]
    translated_keys = set(re.findall(r'"([^"]+)"\s*:', zh_block))
    assert html_keys <= translated_keys


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


def test_backend_modes_keep_single_run_and_campaign_semantics_distinct(tmp_path: Path) -> None:
    state = ApiState(
        tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
        design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
        yosys_bin=ROOT.parent / "bin/yosys",
        runtime_db_path=tmp_path / "runtime.db",
        campaign_db_path=tmp_path / "campaign.db",
    )
    design = state.designs.import_rtl(
        filename="mode_test.v",
        source="module mode_test(input a, output y); assign y = a; endmodule\n",
    )
    detail = state.submit_runtime_design_run({
        "design_id": design["id"], "objective": "timing", "flow_mode": "baseline",
    })
    labels = detail["run"]["task_spec"]["labels"]
    assert labels["objective"] == "timing"
    assert labels["flow_mode"] == "baseline"

    campaign = state.create_stage_campaign({
        "design_id": design["id"], "objective": "area", "flow_mode": "agent",
        "parameter_grid": {"core_utilization_pct": [25, 30, 35]},
        "max_repairs": 2,
    })
    assert len(campaign["members"]) == 3
    assert all(item["status"] == "unbound" for item in campaign["members"])
    member = state.campaign_store.members(campaign["campaign_id"])[0]
    assert member.task_spec.labels["objective"] == "area"
    assert member.task_spec.labels["flow_mode"] == "agent"
