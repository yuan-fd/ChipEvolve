"""Stage 2.4: feedback loop — close the circle from plan to verified lesson.

The loop consumes a reviewed OptimizerPlan, executes it through the scheduler
callback, scores the observed metric, and distills a verified Lesson when the
outcome improved over the prior best. Nothing here executes EDA itself; the
`execute` callback belongs to the scheduler policy layer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .iterative_agent import OptimizerAgent, OptimizerPlan
from .lessons import LessonsStore, Lesson, lesson_from_iteration


@dataclass
class FeedbackOutcome:
    round_no: int
    plan_id: str
    metric: str
    direction: str
    previous_metric: float | None
    observed_metric: float | None
    improved: bool | None
    lesson_id: str | None = None
    status: str = "recorded"       # recorded | failed | skipped
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class FeedbackLoop:
    """2.4 closed loop: plan -> execute -> observe -> distill lesson."""

    def __init__(self, lessons: LessonsStore, agent: OptimizerAgent,
                 context_fingerprint: str):
        self.lessons = lessons
        self.agent = agent
        self.context_fingerprint = context_fingerprint

    def run(self, plan: OptimizerPlan,
            execute: Callable[[OptimizerPlan], Mapping[str, float]],
            previous_metric: float | None = None) -> FeedbackOutcome:
        hypothesis = plan.hypothesis
        metric = hypothesis.metric_name
        direction = hypothesis.direction
        outcome = FeedbackOutcome(
            round_no=plan.round, plan_id=plan.plan_id, metric=metric,
            direction=direction, previous_metric=previous_metric,
            observed_metric=None, improved=None,
        )
        try:
            metrics = dict(execute(plan) or {})
        except Exception as exc:
            outcome.status = "failed"
            return outcome
        observed = metrics.get(metric)
        outcome.observed_metric = observed
        if observed is None:
            outcome.status = "skipped"
            return outcome
        if previous_metric is None:
            outcome.status = "recorded"
            return outcome
        improved = (observed < previous_metric) if direction == "min" \
            else (observed > previous_metric)
        outcome.improved = improved
        if not improved:
            outcome.status = "recorded"
            return outcome
        # Distill a verified lesson only on improvement.
        old_value = next(iter(hypothesis.parameters.values()), 0.0)
        new_value = hypothesis.parameters.get(
            next(iter(hypothesis.parameters.keys()), ""), old_value)
        lesson = lesson_from_iteration(
            context_fingerprint=self.context_fingerprint,
            round_no=plan.round,
            parameter=next(iter(hypothesis.parameters.keys()), "?"),
            old_value=old_value, new_value=new_value,
            metric_name=metric, direction=direction,
            improved=True, metric_value=observed,
            previous_metric=previous_metric,
        )
        if lesson is not None:
            outcome.lesson_id = self.lessons.add(lesson)
        outcome.status = "lesson_distilled"
        return outcome

    def drain_agent_lessons(self, limit: int = 20) -> int:
        """Persist any in-memory optimizer lessons into the durable store."""
        added = 0
        for claim in self.agent.lessons[-limit:]:
            if "improved" not in claim:
                continue
            lesson = Lesson(
                lesson_id=f"lesson-{int(time.time() * 1000)}-{added}",
                kind="parameter_effect", claim=claim,
                context_fingerprint=self.context_fingerprint,
                evidence=[{"source": "optimizer_memory"}],
                confidence=0.5,
            )
            self.lessons.add(lesson)
            added += 1
        return added
