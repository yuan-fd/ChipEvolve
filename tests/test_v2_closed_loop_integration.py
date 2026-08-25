from __future__ import annotations

import platform
import sys
from pathlib import Path

import pytest

from apps.api.app import ApiState
from openroad_platform_contracts import PluginManifest
from openroad_platform_execution import PluginRegistry


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "closed_loop_orfs_adapter.py"


def _state(tmp_path: Path, monkeypatch) -> tuple[ApiState, dict]:
    state = ApiState(
        tmp_path / "platform.db", tmp_path / "uploads", tmp_path / "orfs",
        design_root=tmp_path / "designs", legacy_root=tmp_path / "legacy",
        runtime_db_path=tmp_path / "runtime.db",
        optimization_db_path=tmp_path / "optimization.db",
        load_taiwei_plugin=False,
    )

    def fake_synthesis(_rtl: Path, module: str, directory: Path) -> Path:
        netlist = directory / f"{module}.netlist.v"
        netlist.write_text(_rtl.read_text(encoding="utf-8"), encoding="utf-8")
        return netlist

    monkeypatch.setattr(state.designs, "_synthesize", fake_synthesis)
    design = state.designs.import_rtl(
        filename="closed_loop_top.v",
        source=("module closed_loop_top(input clk, input a, output reg y); "
                "always @(posedge clk) y <= a; endmodule\n"),
    )
    kinds = ("report", "odb", "config", "toolchain_snapshot", "run_result",
             "def", "netlist", "gds")
    manifest = PluginManifest(
        plugin_id="orfs", plugin_version="1.0.0",
        adapter_entry=(sys.executable, str(FIXTURE)),
        capabilities=("eda.rtl_to_gds",), supported_arch=(platform.machine(),),
        input_schema={"type": "object"}, output_schema={"type": "object"},
        artifact_rules=tuple({"kind": kind, "required": kind != "report"}
                             for kind in kinds),
        default_timeout_seconds=30,
    )
    state.runtime.registry = PluginRegistry([manifest])
    return state, design


def _payload(design_id: str) -> dict:
    return {
        "design_id": design_id, "experiment_key": "integration-resume",
        "objective_profile": "balanced", "repetitions": 3,
        "max_rounds": 6, "stall_window": 3,
        "minimum_relative_improvement": .25,
        "optimizer_seed": 20260824,
        "replica_or_seeds": [10101, 20202, 30303],
        "parameter_space": {
            "core_utilization_pct": [20.0, 65.0],
            "place_density": [.35, .78],
        },
    }


def test_closed_loop_runs_replicas_to_three_stall_diagnosis_and_resumes(tmp_path, monkeypatch):
    state, design = _state(tmp_path, monkeypatch)
    created = state.start_bayesian_closed_loop(_payload(design["id"]))
    assert created["execution_started"] is True
    assert len(created["state"]["active_run_ids"]) == 3

    result = state.run_bayesian_closed_loop_to_boundary(
        created["pipeline_id"], {"max_transitions": 16})
    loop = result["state"]
    assert loop["status"] == "diagnosis_required"
    assert loop["stalled_rounds"] == 3
    assert [item["kind"] for item in loop["history"]] == [
        "baseline", "bo_candidate", "bo_candidate", "bo_candidate",
    ]
    assert all(item["summary"]["replicas"] == 3 for item in loop["history"])
    assert all(item["summary"]["failure_rate"] == 0 for item in loop["history"])
    assert all("iqr" in item["summary"]["metrics"]["area_um2"]
               for item in loop["history"])
    assert len(state.runtime_store.list_runs()) == 12
    assert len(state.optimization_store.observations(loop["study_id"])) == 12
    assert state.optimization_store.get(loop["study_id"]).seed == 20260824
    for item in loop["history"]:
        seeds = [state.runtime_store.get_run(run_id).task_spec.parameters["or_seed"]
                 for run_id in item["summary"]["run_ids"]]
        assert seeds == [10101, 20202, 30303]
    phases = [item["phase"] for item in loop["agent_events"]]
    assert phases[:3] == ["map", "semantic", "experiment"]
    assert {"hypothesis", "implement", "validate", "review",
            "memory", "diagnosis"} <= set(phases)
    assert all(item.get("run_ids") for item in loop["agent_events"]
               if item["phase"] == "validate")
    assert all(item.get("run_ids") for item in loop["agent_events"]
               if item["phase"] == "memory" and "outcome" in item)
    causal = loop["diagnosis"]["causal_hypothesis"]
    assert causal["status"] == "draft"
    assert causal["proposed_intervention"]["kind"] == "preregistered_2x2_interaction"
    assert causal["proposed_intervention"]["execution_allowed"] is False
    assert state.hypothesis_ledger.history(causal["hypothesis_id"])

    # A repeated HTTP-equivalent resume is a read, not another experiment.
    resumed = state.run_bayesian_closed_loop_to_boundary(
        created["pipeline_id"], {"max_transitions": 16})
    assert resumed["state"]["history"] == loop["history"]
    assert len(state.runtime_store.list_runs()) == 12


def test_closed_loop_freezes_spec_clock_outside_bo_space(tmp_path, monkeypatch):
    state, design = _state(tmp_path, monkeypatch)
    payload = _payload(design["id"])
    payload["parameter_space"] = {
        "core_utilization_pct": [20.0, 65.0],
        "place_density": [.35, .78],
        "clock_period_ns": [5.0, 20.0],
    }
    with pytest.raises(ValueError, match="unsupported.*clock_period_ns"):
        state.start_bayesian_closed_loop(payload)


def test_all_infeasible_search_ends_at_diagnosis_not_false_completed(tmp_path, monkeypatch):
    state, design = _state(tmp_path, monkeypatch)
    payload = _payload(design["id"])
    payload.update({
        "experiment_key": "all-infeasible",
        "max_rounds": 1,
        "hard_constraints": [
            {"metric": "drc_errors", "operator": "<=", "threshold": -1.0}],
    })
    created = state.start_bayesian_closed_loop(payload)
    result = state.run_bayesian_closed_loop_to_boundary(
        created["pipeline_id"], {"max_transitions": 8})["state"]

    assert result["status"] == "diagnosis_required"
    assert result["best_feasible"] is False
    assert result["best_utility"] == -1.0
    assert result["history"][0]["utility"] is None
    assert result["diagnosis"]["reason"] == (
        "no hard-constraint-feasible baseline or candidate")


def test_research_flow_timeout_is_bounded_and_frozen_in_every_replica(tmp_path, monkeypatch):
    state, design = _state(tmp_path, monkeypatch)
    payload = _payload(design["id"])
    payload.update({"stage_timeout_seconds": 7200, "flow_timeout_seconds": 14400})
    created = state.start_bayesian_closed_loop(payload)
    for run_id in created["state"]["active_run_ids"]:
        task = state.runtime_store.get_run(run_id).task_spec
        assert task.parameters["stage_timeout_seconds"] == 7200
        assert task.timeout_seconds == 14400
    payload["stage_timeout_seconds"] = 14_401
    payload["experiment_key"] = "invalid-timeout"
    with pytest.raises(ValueError, match="stage_timeout_seconds"):
        state.start_bayesian_closed_loop(payload)


def test_closed_loop_replica_submission_recovers_missing_suffix(tmp_path, monkeypatch):
    state, design = _state(tmp_path, monkeypatch)
    original_submit = state.runtime.submit
    calls = 0

    def interrupted_submit(task, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected process interruption")
        return original_submit(task, **kwargs)

    monkeypatch.setattr(state.runtime, "submit", interrupted_submit)
    with pytest.raises(RuntimeError, match="injected"):
        state.start_bayesian_closed_loop(_payload(design["id"]))
    checkpoint = state.pipeline_checkpoints.create_or_get(
        pipeline_kind="bo-gp-closed-loop-v2", subject_id="integration-resume",
        owner_id=None, initial_state={},
    )
    assert len(checkpoint["state"]["active_run_ids"]) == 1

    monkeypatch.setattr(state.runtime, "submit", original_submit)
    resumed = state.start_bayesian_closed_loop(_payload(design["id"]))
    ids = resumed["state"]["active_run_ids"]
    assert len(ids) == len(set(ids)) == 3
    assert len(state.runtime_store.list_runs()) == 3


def test_candidate_round_recovers_partial_replica_submission(tmp_path, monkeypatch):
    state, design = _state(tmp_path, monkeypatch)
    created = state.start_bayesian_closed_loop(_payload(design["id"]))
    original_submit = state.runtime.submit
    calls = 0

    def interrupted_submit(task, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("candidate submission interrupted")
        return original_submit(task, **kwargs)

    monkeypatch.setattr(state.runtime, "submit", interrupted_submit)
    with pytest.raises(RuntimeError, match="candidate submission"):
        state.run_bayesian_closed_loop_to_boundary(
            created["pipeline_id"], {"max_transitions": 1})
    interrupted = state.pipeline_checkpoints.get(created["pipeline_id"])["state"]
    assert interrupted["active_kind"] == "bo_candidate"
    assert interrupted["round"] == 1
    assert interrupted["active_proposal_id"]
    assert len(interrupted["active_run_ids"]) == 1

    monkeypatch.setattr(state.runtime, "submit", original_submit)
    finished = state.run_bayesian_closed_loop_to_boundary(
        created["pipeline_id"], {"max_transitions": 16})["state"]
    assert finished["status"] == "diagnosis_required"
    first_round = [item for item in finished["history"] if item["round"] == 1][0]
    assert first_round["summary"]["replicas"] == 3
    round_runs = [state.runtime_store.get_run(run_id)
                  for run_id in first_round["summary"]["run_ids"]]
    assert len({run.run_id for run in round_runs}) == 3
    assert all(run.task_spec.labels["optimizer_proposal_id"]
               == interrupted["active_proposal_id"] for run in round_runs)


def test_objective_profile_is_an_effective_ranking_policy(tmp_path, monkeypatch):
    state, _ = _state(tmp_path, monkeypatch)
    baseline = {
        "eligible": True, "complete_objectives": True, "successes": 3, "replicas": 3,
        "metrics": {
            "area_um2": {"median": 100.0},
            "setup_wns_ns": {"median": 1.0},
            "power_W": {"median": 1.0},
        },
    }
    smaller_but_slower = {
        **baseline, "metrics": {
            "area_um2": {"median": 80.0},
            "setup_wns_ns": {"median": .8},
            "power_W": {"median": 1.0},
        },
    }
    faster_but_larger = {
        **baseline, "metrics": {
            "area_um2": {"median": 120.0},
            "setup_wns_ns": {"median": 1.2},
            "power_W": {"median": 1.0},
        },
    }
    from openroad_platform_analysis import relative_utility
    assert relative_utility(smaller_but_slower, baseline, state._v2_objectives("area")) > 0
    assert relative_utility(faster_but_larger, baseline, state._v2_objectives("area")) < 0
    assert relative_utility(smaller_but_slower, baseline, state._v2_objectives("timing")) < 0
    assert relative_utility(faster_but_larger, baseline, state._v2_objectives("timing")) > 0


def test_second_study_warm_starts_from_verified_exact_context_memory(tmp_path, monkeypatch):
    state, design = _state(tmp_path, monkeypatch)
    first = state.start_bayesian_closed_loop(_payload(design["id"]))
    state.run_bayesian_closed_loop_to_boundary(
        first["pipeline_id"], {"max_transitions": 16})

    second_payload = {**_payload(design["id"]),
                      "experiment_key": "second-memory-study"}
    second = state.start_bayesian_closed_loop(second_payload)
    observed = state.run_bayesian_closed_loop_to_boundary(
        second["pipeline_id"], {"max_transitions": 1})["state"]
    assert len(observed["memory_prior_observations"]) == 12
    assert len(observed["memory_prior_refs"]) == 12
    assert observed["validated_knowledge_bundle"]["bundle_fingerprint"]
    proposal = state.optimization_store.proposals(observed["study_id"])[0]
    # Iteration counts only the new study's three baseline replicas; prior
    # evidence is cited and used by the GP without pretending it was rerun.
    assert proposal.iteration == 3
    prior_refs = {pointer.ref for pointer in proposal.evidence}
    assert any(ref.startswith("run:") for ref in prior_refs)
