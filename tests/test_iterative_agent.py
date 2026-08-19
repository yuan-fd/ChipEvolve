"""Stage 1 acceptance tests: Optimizer loop, Disruptor, ledger, headroom,
analysis layer, and the Coder interface (planning only, never executes)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from openroad_platform_analysis.agent_trace import AgentTraceStore
from openroad_platform_analysis.iterative_agent import (
    AnalysisLayer, CoderAgent, DisruptorAgent, HeadroomLedger, IterationLedger,
    IterationState, OptimizerAgent, OptimizerHypothesis, OptimizerPlan,
)


def _make_agent(tmp: Path, metric: str = "worst_slack", direction: str = "min",
                max_rounds: int = 10):
    ledger = IterationLedger(tmp / "iterations.jsonl")
    traces = AgentTraceStore(tmp / "traces.db")
    return OptimizerAgent(
        ledger, trace_store=traces,
        parameter_bounds={"utilization": (20.0, 80.0), "density": (0.3, 0.9)},
        metric=metric, direction=direction, max_rounds=max_rounds,
    ), ledger, traces


def test_iteration_ledger_roundtrip(tmp_path: Path):
    ledger = IterationLedger(tmp_path / "state.jsonl")
    ledger.append(IterationState(round=1, parameters={"utilization": 30.0},
                                 status="pending"))
    ledger.replace_round(1, IterationState(
        round=1, parameters={"utilization": 30.0},
        metrics={"worst_slack": -0.4}, score=1.0, status="succeeded"))
    states = ledger.read()
    assert len(states) == 1
    assert states[0].round == 1
    assert states[0].metrics["worst_slack"] == -0.4
    assert states[0].status == "succeeded"


def test_optimizer_cold_start_plan(tmp_path: Path):
    agent, ledger, traces = _make_agent(tmp_path)
    trace = traces.create("optimize utilization", "optimizer")
    result = agent.run_iteration(trace=trace)
    assert result["status"] == "pending_review"
    assert result["round"] == 1
    plan = result["plan"]
    assert plan["execution_allowed"] is False
    assert plan["required_gate"] == "human_review"
    assert "utilization" in plan["hypothesis"]["parameters"]
    # pending state recorded before execution
    states = ledger.read()
    assert states[-1].round == 1
    assert states[-1].status == "pending"


def test_optimizer_score_and_improvement(tmp_path: Path):
    agent, ledger, traces = _make_agent(tmp_path)
    # round 1: baseline (worse slack)
    agent.run_iteration(trace=traces.create("round1", "optimizer"))
    ledger.replace_round(1, IterationState(
        round=1, parameters={"utilization": 50.0, "density": 0.6},
        metrics={"worst_slack": -0.7}, score=1.0, status="succeeded"))
    # round 2 with better metric (more negative slack) -> score > 0
    result = agent.run_iteration(trace=traces.create("round2", "optimizer"))
    assert result["round"] == 2
    recorded = result["state"]
    assert recorded["status"] == "pending"  # observation not yet backfilled
    # backfill round 2 as an improvement
    agent.record_observation(2, {"utilization": 50.0, "density": 0.6},
                             {"worst_slack": -1.0}, status="succeeded")
    states = ledger.read()
    assert states[-1].score > 0
    assert any("improved" in note for note in agent.lessons)


def test_disruptor_stall_detection():
    disruptor = DisruptorAgent(stall_window=3, tolerance=0.01)
    trend = AnalysisLayer().dynamic_trend([
        IterationState(round=1, parameters={}, metrics={"worst_slack": -1.0},
                       status="succeeded"),
        IterationState(round=2, parameters={}, metrics={"worst_slack": -1.01},
                       status="succeeded"),
        IterationState(round=3, parameters={}, metrics={"worst_slack": -1.0},
                       status="succeeded"),
    ], metric="worst_slack", direction="min")
    check = disruptor.check(trend)
    assert check["stalled"] is True
    assert check["redirect"]["kind"] == "widen_exploration"
    assert check["redirect"]["suggested"]["exploration"] > 0
    # non-stalled trend
    trend2 = AnalysisLayer().dynamic_trend([
        IterationState(round=1, parameters={}, metrics={"worst_slack": -1.0},
                       status="succeeded"),
        IterationState(round=2, parameters={}, metrics={"worst_slack": -0.5},
                       status="succeeded"),
    ], metric="worst_slack", direction="min")
    assert disruptor.check(trend2)["stalled"] is False


def test_static_attribution_and_headroom(tmp_path: Path):
    agent, ledger, traces = _make_agent(tmp_path)
    # synthetic observations with a clear positive correlation for utilization
    for round_no, util, slack in ((1, 20.0, -2.0), (2, 40.0, -1.2),
                                  (3, 60.0, -0.5), (4, 80.0, 0.2)):
        ledger.append(IterationState(
            round=round_no, parameters={"utilization": util, "density": 0.6},
            metrics={"worst_slack": slack}, score=1.0, status="succeeded"))
    analysis = agent.analyze(ledger.read())
    attribution = analysis["attribution"]
    util_attr = next(item for item in attribution if item.parameter == "utilization")
    assert util_attr.sample_count == 4
    assert util_attr.correlation > 0.5  # higher util -> better slack
    # headroom ledger
    from openroad_platform_analysis.iterative_agent import HeadroomEntry
    headroom = HeadroomLedger()
    headroom.register(HeadroomEntry(
        hypothesis_id="h1", metric_name="worst_slack", direction="min",
        baseline_value=-0.5, expected_value=-1.5, budget_remaining=5))
    assert headroom.rank_remaining(1)[0].expected_gain > 0.5
    hypothesis = OptimizerHypothesis(
        hypothesis_id="h1", rationale="test",
        parameters={"utilization": 70.0}, metric_name="worst_slack",
        direction="min", expected_value=-0.8)
    plan = OptimizerPlan(plan_id="p1", hypothesis=hypothesis, round=1,
                         budget_remaining=5)
    assert plan.to_dict()["required_gate"] == "human_review"


def test_coder_agent_planning_only():
    coder = CoderAgent()
    plan = coder.propose_edit(
        objective="reduce cell count",
        baseline_ref="run:abc",
        candidates=[{"kind": "synthesis", "description": "retime critical path",
                     "risk": "low"},
                    {"kind": "unknown", "description": "", "risk": "high"}],
        context={"design": "gcd"},
    )
    assert plan["execution_allowed"] is False
    assert plan["required_gate"] == "coding_agent_isolated_validation"
    assert len(plan["candidates"]) == 1
    assert plan["candidates"][0]["candidate_id"].startswith("code-")


def test_optimizer_max_rounds(tmp_path: Path):
    agent, ledger, traces = _make_agent(tmp_path, max_rounds=10)
    for _ in range(11):
        result = agent.run_iteration(trace=traces.create("loop", "optimizer"))
    assert result["status"] == "budget_exhausted"
