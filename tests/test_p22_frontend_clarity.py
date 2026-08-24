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
    assert "Automatic dual-agent flow" in html
    assert "/run-to-baseline" in javascript
    assert "/auto-rtlscout" not in javascript
    assert "TCADCraft" in javascript and "MoMCraft" in javascript
    assert "CktCraft" in javascript  # craft extensions render dynamically via /api/platform
    assert "RTLCraft" not in html and "EDACode" not in html
    assert ".code-viewer" in css and "background: #fff" in css
    assert "Automatic policy" in html and "No API key or expert knob" in html
    assert "saveProvider" not in javascript and "providerKey" not in javascript
    assert 'model: "gpt-5.6-terra"' not in javascript
    assert 'provider: "codex-cli"' not in javascript
    assert "gpt-5.6-sol" not in javascript
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
    assert html.index("Choose a registered RTL design") < html.index("Choose constraints and optimization goal")
    assert html.index("Choose constraints and optimization goal") < html.index("Run RTL-to-GDS and monitor stages")
    assert html.index("Run RTL-to-GDS and monitor stages") < html.index("Layout, QoR, and implementation evidence")
    assert 'id="flowProgressBar"' in html
    assert 'id="backendDesignChips"' in html
    assert ".task-panel .stage" in css and "grid-template-columns: 18px 95px 1fr 55px" in css
    assert "attempt.metrics" in javascript
    assert 'post("/api/v2/closed-loops", base)' in javascript
    assert "repetitions: 3" not in javascript and "stall_window: 3" not in javascript
    assert "max_transitions: 64" not in javascript
    assert 'run-to-boundary`, {})' in javascript
    assert 'id="flowUtil"' not in html
    assert 'id="flowDensity"' not in html
    assert 'id="flowPeriod"' not in html
    assert 'id="flowTarget"' not in html
    assert 'id="rtlscoutCost"' not in html
    assert 'id="rtlscoutSteps"' not in html
    assert 'id="rtlscoutMode"' not in html
    assert "/collect-learning" not in javascript
    assert "/api/campaigns/stage-aware" not in javascript
    assert "recommendationList" not in html and "recommendationList" not in javascript
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
    assert '"backend.run.action": "开始 Agent 自主 BO/GP 优化"' in javascript
    assert 'localStorage.setItem("openroad-platform-locale"' in javascript
    assert 'document.documentElement.lang = state.locale === "zh" ? "zh-CN" : "en"' in javascript
    html_keys = set(re.findall(r'data-i18n="([^"]+)"', html))
    zh_block = javascript.split("const ZH = {", 1)[1].split("};", 1)[0]
    translated_keys = set(re.findall(r'"([^"]+)"\s*:', zh_block))
    assert html_keys <= translated_keys


def test_rtlscout_benchmark_only_entry_is_deleted(tmp_path: Path) -> None:
    state = ApiState(
        tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
        design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
        yosys_bin=ROOT.parent / "bin/yosys",
        runtime_db_path=tmp_path / "runtime.db",
    )
    assert not hasattr(state, "submit_rtlscout")


def test_product_state_exposes_only_the_autonomous_bogp_business_path(tmp_path: Path) -> None:
    """Old tutorial modes must be deleted, not merely hidden in the browser."""
    state = ApiState(
        tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
        design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
        yosys_bin=ROOT.parent / "bin/yosys",
        runtime_db_path=tmp_path / "runtime.db",
    )
    removed = {
        "begin_four_gate_baseline", "start_evolution_campaign",
        "create_stage_campaign", "submit_runtime_design_run",
        "run_optimizer_iteration", "create_recommendation",
        "decide_recommendation", "submit_campaign",
        "submit_run",
    }
    assert all(not hasattr(state, name) for name in removed)
    assert hasattr(state, "start_bayesian_closed_loop")
    assert hasattr(state, "run_bayesian_closed_loop_to_boundary")

    # The autonomous Agent trace uses one stable vocabulary shared by the
    # dashboard, experiment exporter and paper figures.
    source = (ROOT / "apps/api/app.py").read_text(encoding="utf-8")
    assert '"phase": "implementation"' not in source
    assert '"phase": "validation"' not in source

    api_source = source
    assert 'path == "/api/v2/closed-loops"' in api_source
    assert "/api/campaigns/stage-aware" not in api_source
    assert "/api/four-gate/" not in api_source
    assert "/api/providers" not in api_source
    assert "/api/optimization/studies" not in api_source
    assert not hasattr(state, "list_optimization_studies")
    assert not hasattr(state, "get_optimization_study")

    # BYOK was deleted from the execution boundary too, not merely hidden in
    # the browser.  RTLScout cannot request third-party API credentials.
    rtl_plugin = (ROOT / "packages/execution/src/openroad_platform_execution/rtlscout_plugin.py").read_text(encoding="utf-8")
    for legacy_provider in ("anthropic", "deepinfra", "openrouter", "API_KEY"):
        assert legacy_provider not in rtl_plugin
