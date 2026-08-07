from __future__ import annotations

import re
from pathlib import Path

from apps.api.app import ApiState


ROOT = Path(__file__).resolve().parents[1]


def make_state(tmp_path: Path) -> ApiState:
    return ApiState(
        tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
        design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
        yosys_bin=tmp_path / "missing-yosys",
        runtime_db_path=tmp_path / "runtime.db",
        campaign_db_path=tmp_path / "campaign.db",
    )


def test_web_has_five_clear_bilingual_primary_tabs():
    html = (ROOT / "apps" / "web" / "index.html").read_text(encoding="utf-8")
    tabs = re.findall(
        r'<button class="tab(?: active)?" data-route="[^"]+"[^>]*>([^<]+)</button>', html
    )
    assert tabs == [
        "Overview", "Frontend Design", "Backend Design",
        "Projects &amp; Results", "Self-Evolution",
    ]
    assert '<html lang="en">' in html
    assert 'id="langZh"' in html and 'id="langEn"' in html
    assert "IC Craft" not in html


def test_web_uses_a_restrained_minimal_visual_system():
    css = (ROOT / "apps" / "web" / "assets" / "app.css").read_text(encoding="utf-8")
    assert "--accent: #2563eb" in css
    assert "--radius: 6px" in css
    assert "Georgia" not in css
    assert ".embedded-extension-detail:not(:empty)" in css
    assert "box-shadow: 0 18px" not in css


def test_platform_read_model_matches_five_page_information_architecture(tmp_path):
    state = make_state(tmp_path)
    snapshot = state.platform.snapshot()
    assert [item["label"] for item in snapshot["navigation"]] == [
        "Overview", "Frontend Design", "Backend Design",
        "Projects & Results", "Self-Evolution",
    ]
    components = snapshot["extensions"]["components"]
    assert len(components) == 6
    assert len({item["plugin_id"] for item in components}) == 6
    assert snapshot["architecture"]["control_plane"].startswith("Workflow Runtime")


def test_results_projection_uses_registered_design_and_runtime_records(tmp_path):
    state = make_state(tmp_path)
    result = state.submit_edacraft_smoke("edacode")
    records = state.platform.results()["records"]
    record = next(item for item in records if item["id"] == result["run"]["run"]["run_id"])
    assert record["record_type"] == "runtime_run"
    assert record["plugin_id"] == "edacraft-edacode"
    assert record["status"] == "queued"
    assert result["execution_started"] is False
