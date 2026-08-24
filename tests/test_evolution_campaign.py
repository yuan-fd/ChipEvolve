import sys
from pathlib import Path

import pytest

from openroad_platform_contracts import PluginManifest, TaskSpec
from openroad_platform_execution import PluginRegistry
from openroad_platform_scheduler import (EvolutionCampaign, EvolutionCampaignController,
                                         EvolutionCampaignStore, RuntimeStore, WorkflowRuntime)


FIXTURE = Path(__file__).parent / "fixtures" / "echo_adapter.py"


def test_runtime_campaign_runs_replicas_then_stops_on_stall(tmp_path):
    plugin = PluginManifest("echo", "1.0.0", (sys.executable, str(FIXTURE)), ("test.echo",),
                            ("x86_64", "aarch64"), {}, {}, artifact_rules=({"kind": "report", "required": True},))
    runtime = WorkflowRuntime(RuntimeStore(tmp_path / "runtime.db"), PluginRegistry([plugin]),
                              workspace_root=tmp_path / "runs")
    controller = EvolutionCampaignController(EvolutionCampaignStore(tmp_path / "evolution.db"), runtime)
    base = TaskSpec("base", "p", "d", plugin_id="echo", inputs={"message": "x"},
                    parameters={"knob": 1.0})
    started = controller.start(EvolutionCampaign("campaign-1", base, "knob", (2.0, 3.0),
                                                  repetitions=2, max_rounds=2, stall_window=1,
                                                  objective_metric="messages"))
    assert started["state"]["status"] == "baseline_running"
    first = controller.advance("campaign-1")
    assert first["state"]["status"] == "round_running"
    stopped = controller.advance("campaign-1")
    assert stopped["state"]["status"] == "diagnosis_required"
    assert stopped["state"]["redirect"]["automatic_execution"] is False
    assert len(stopped["state"]["history"]) == 2
    assert all(len(item["run_ids"]) == 2 for item in stopped["state"]["history"])


def test_stall_automatically_redirects_only_to_predeclared_second_parameter(tmp_path):
    plugin = PluginManifest("echo", "1.0.0", (sys.executable, str(FIXTURE)), ("test.echo",),
                            ("x86_64", "aarch64"), {}, {}, artifact_rules=({"kind": "report", "required": True},))
    runtime = WorkflowRuntime(RuntimeStore(tmp_path / "runtime.db"), PluginRegistry([plugin]),
                              workspace_root=tmp_path / "runs")
    controller = EvolutionCampaignController(EvolutionCampaignStore(tmp_path / "evolution.db"), runtime)
    base = TaskSpec("base", "p", "d", plugin_id="echo", inputs={"message": "x"},
                    parameters={"knob": 1.0, "density": .45})
    controller.start(EvolutionCampaign("campaign-redirect", base, "knob", (2.0,),
                                      repetitions=2, max_rounds=1, stall_window=1,
                                      objective_metric="messages", redirect_parameter="density",
                                      redirect_values=(.55,)))
    assert controller.advance("campaign-redirect")["state"]["status"] == "round_running"
    redirected = controller.advance("campaign-redirect")
    assert redirected["state"]["status"] == "redirect_running"
    assert redirected["state"]["redirect"]["parameter"] == "density"
    assert redirected["state"]["redirect"]["automatic_execution"] is True
    for run_id in redirected["state"]["active_run_ids"]:
        assert runtime.store.get_run(run_id).task_spec.parameters["density"] == .55
        assert runtime.store.get_run(run_id).task_spec.parameters["knob"] == 1.0
    finished = controller.advance("campaign-redirect")
    assert finished["state"]["status"] == "completed"


def test_redirect_rejects_an_undeclared_or_primary_parameter(tmp_path):
    base = TaskSpec("base", "p", "d", plugin_id="echo", parameters={"knob": 1.0})
    with pytest.raises(ValueError, match="differ"):
        EvolutionCampaign("same", base, "knob", (2.0,), 1, 1, 1, "messages",
                          redirect_parameter="knob", redirect_values=(3.0,)).validate()
    with pytest.raises(ValueError, match="declared"):
        EvolutionCampaign("unknown", base, "knob", (2.0,), 1, 1, 1, "messages",
                          redirect_parameter="not_allowed", redirect_values=(3.0,)).validate()
