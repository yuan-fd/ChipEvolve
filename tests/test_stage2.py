"""Stage 2 acceptance tests: lessons store + distillation, skills store +
application, feedback loop, and behavior-cloning real-data training path."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from openroad_platform_analysis.agent_trace import AgentTraceStore
from openroad_platform_analysis.feedback_loop import FeedbackLoop
from openroad_platform_analysis.iterative_agent import (
    IterationLedger, OptimizerAgent, OptimizerHypothesis, OptimizerPlan,
)
from openroad_platform_analysis.lessons import (
    LessonsStore, distill_lesson, lesson_from_iteration,
)
from openroad_platform_analysis.offline_policy import (
    BehaviorCloningShadowPolicy, build_trajectory, split_by_design,
)
from openroad_platform_analysis.skills import (
    Skill, SkillsStore, apply_skill,
)
from openroad_platform_contracts import (
    LearningContext, LearningObservation, EvidencePointer, ObjectiveSpec,
)


def test_lessons_store_roundtrip(tmp_path: Path):
    store = LessonsStore(tmp_path / "lessons.db")
    lesson = distill_lesson(
        kind="parameter_effect",
        claim="raising utilization improved worst_slack",
        context_fingerprint="ctx-1",
        evidence=[{"round": 3, "metric_name": "worst_slack", "metric_value": -0.9}],
        confidence=0.7,
        tags=("utilization", "worst_slack"),
    )
    store.add(lesson)
    hits = store.search("ctx-1")
    assert len(hits) == 1
    assert hits[0]["claim"].startswith("raising utilization")
    assert store.count() == 1


def test_lesson_distillation_only_on_improvement():
    improved = lesson_from_iteration(
        context_fingerprint="ctx-1", round_no=3, parameter="utilization",
        old_value=30.0, new_value=50.0, metric_name="worst_slack",
        direction="min", improved=True, metric_value=-1.0, previous_metric=-0.7,
    )
    assert improved is not None and improved.kind == "parameter_effect"
    # no improvement -> no lesson (failed runs never become ground truth)
    assert lesson_from_iteration(
        context_fingerprint="ctx-1", round_no=4, parameter="utilization",
        old_value=50.0, new_value=30.0, metric_name="worst_slack",
        direction="min", improved=False, metric_value=-0.5, previous_metric=-1.0,
    ) is None


def test_skills_store_match_and_apply(tmp_path: Path):
    store = SkillsStore(tmp_path / "skills.db")
    skill = Skill(
        skill_id="skill-1", name="density-tighten",
        description="tighten density after routing congestion",
        trigger_terms=("routing", "congestion", "density"),
        parameter_template={"density": {"bounds": (0.3, 0.9), "default": 0.7}},
        lesson_ids=("lesson-1",),
    )
    store.add(skill)
    matches = store.match("routing congestion observed")
    assert len(matches) == 1
    assert matches[0]["score"] > 0
    plan = apply_skill(skill, "routing congestion", {"density": 0.5})
    assert plan["adjustments"]["density"] == 0.7
    assert plan["execution_allowed"] is False
    assert plan["required_gate"] == "human_review"


def test_feedback_loop_distills_lesson(tmp_path: Path):
    lessons = LessonsStore(tmp_path / "lessons.db")
    ledger = IterationLedger(tmp_path / "iterations.jsonl")
    traces = AgentTraceStore(tmp_path / "traces.db")
    agent = OptimizerAgent(
        ledger, trace_store=traces,
        parameter_bounds={"utilization": (20.0, 80.0)},
        metric="worst_slack", direction="min", max_rounds=5,
    )
    hypothesis = OptimizerHypothesis(
        hypothesis_id="h1", rationale="test", parameters={"utilization": 60.0},
        metric_name="worst_slack", direction="min", expected_value=-0.8)
    plan = OptimizerPlan(plan_id="p1", hypothesis=hypothesis, round=1,
                         budget_remaining=3)
    loop = FeedbackLoop(lessons, agent, context_fingerprint="ctx-1")

    improved = loop.run(plan, lambda _p: {"worst_slack": -1.2},
                        previous_metric=-0.7)
    assert improved.status == "lesson_distilled"
    assert improved.improved is True
    assert improved.lesson_id is not None
    assert lessons.count() == 1

    regressed = loop.run(plan, lambda _p: {"worst_slack": -0.5},
                         previous_metric=-1.2)
    assert regressed.improved is False
    assert regressed.lesson_id is None
    assert lessons.count() == 1  # no new lesson on regression


def test_behavior_cloning_real_data_training(tmp_path: Path):
    """2.3: BC shadow policy trains on real observations (verified path)."""
    objective = ObjectiveSpec(metric_name="worst_slack", direction="min", weight=1.0)
    trajectory = []
    for design_id in ("design-a", "design-b"):
        context = LearningContext(
            design_id=design_id, platform="nangate45", pdk_id="nangate45",
            toolchain_id="openroad-v1",
            design_fingerprint=("a" if design_id == "design-a" else "b") * 64,
            flow_stage="finish", metric_parser_version="orfs-v1",
        )
        observations = []
        for index, (util, slack) in enumerate(
                ((30.0, -1.0), (40.0, -0.8), (50.0, -0.5), (60.0, -0.3)), start=1):
            observations.append(LearningObservation(
                observation_id=f"{design_id}-obs-{index}", context=context,
                parameters={"utilization": util},
                metrics={"worst_slack": slack},
                metric_units={"worst_slack": "ns"},
                status="succeeded", cost_seconds=100.0 + index,
                run_id=f"{design_id}-run-{index}",
                attempt_id=f"{design_id}-attempt-{index}",
                evidence=(EvidencePointer(
                    ref=f"artifact:run:{design_id}:{index}", sha256="0" * 64),),
            ))
        trajectory += build_trajectory(observations, [objective],
                                       trajectory_id=f"tr-{design_id}")
    train, held_out = split_by_design(trajectory, {"design-b"})
    assert len(train) >= 2 and len(held_out) >= 2
    policy = BehaviorCloningShadowPolicy().fit(train)
    state = {name: float(value) for name, value in {
        "worst_slack": -0.3, "failed": 0.0}.items()}
    proposal = policy.propose(
        design_id="design-a",
        context_fingerprint=(
            LearningContext(
                design_id="design-a", platform="nangate45", pdk_id="nangate45",
                toolchain_id="openroad-v1",
                design_fingerprint="a" * 64,
                flow_stage="finish", metric_parser_version="orfs-v1",
            ).fingerprint),
        state=state,
        evidence=(EvidencePointer(ref="artifact:policy:bc", sha256="0" * 64),),
    )
    assert proposal.execution_allowed is False
    assert "utilization" in proposal.action
