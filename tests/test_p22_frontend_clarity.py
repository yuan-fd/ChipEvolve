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
    assert "TCADCraft" in javascript and "MoMCraft" in javascript
    assert "CktCraft" in javascript  # craft extensions render dynamically via /api/platform
    assert "RTLCraft" not in html and "EDACode" not in html
    assert ".code-viewer" in css and "background: #fff" in css
    assert "Platform model" in html and "no user API key is accepted" in html
    assert "saveProvider" not in javascript and "providerKey" not in javascript
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
    assert "2^3" in javascript and "max_total_runs: 8" in javascript
    assert "state.designs[0]" not in javascript
    assert "physicalRuns[0]" not in javascript
    assert "Design\", \"设计" in javascript and "Run\", \"任务" in javascript
    assert "runtime_worker_ready" in javascript
    assert "renderBackendEvidence" in javascript
    assert "paintDensityHeatmap" in javascript
    api_source = (ROOT / "apps/api/app.py").read_text(encoding="utf-8")
    assert "OpenDB database" in api_source and '"1_synth", "synth", "Synthesis"' in api_source
    assert "Run bounded smoke" not in javascript
    assert "Current design" in javascript and "Main-flow evidence" in javascript
    assert "/api/extensions/taiwei/run" in javascript


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


def test_rtlscout_web_rejects_removed_benchmark_only_entry(tmp_path: Path) -> None:
    state = ApiState(
        tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
        design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
        yosys_bin=ROOT.parent / "bin/yosys",
        runtime_db_path=tmp_path / "runtime.db",
        campaign_db_path=tmp_path / "campaign.db",
    )
    with pytest.raises(RuntimeError, match="removed in v2"):
        state.submit_rtlscout({
        "mode": "offline_demo", "benchmark": "simple_adder",
        "cost_metric": "yosys_cells", "max_steps": 3,
        "api_key": "must-not-enter-task",
        })


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
        "platform": "sky130hd",
    })
    labels = detail["run"]["task_spec"]["labels"]
    assert labels["objective"] == "timing"
    assert labels["flow_mode"] == "baseline"
    assert detail["run"]["task_spec"]["parameters"]["platform"] == "sky130hd"

    campaign = state.create_stage_campaign({
        "design_id": design["id"], "objective": "area", "flow_mode": "agent",
        "platform": "sky130hs",
        "parameter_grid": {"core_utilization_pct": [25, 30, 35]},
        "max_repairs": 2,
    })
    assert len(campaign["members"]) == 3
    assert all(item["status"] == "unbound" for item in campaign["members"])
    member = state.campaign_store.members(campaign["campaign_id"])[0]
    assert member.task_spec.labels["objective"] == "area"
    assert member.task_spec.labels["flow_mode"] == "agent"
    assert member.task_spec.parameters["platform"] == "sky130hs"
