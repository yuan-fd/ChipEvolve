"""Iterative agent architecture for the self-evolving platform.

Stage 1 (1.1-1.6) of the platform upgrade:

- 1.1 OptimizerAgent   — iterate: analyze -> hypothesize -> execute -> score
                         -> record -> lessons (with AgentTrace visibility)
- 1.2 DisruptorAgent   — stall detection (no improvement over a window) and
                         redirection (widen exploration, shift objective focus)
- 1.3 CoderAgent       — future source-level optimization agent (interface +
                         non-executable plan placeholder)
- 1.4 IterationLedger  — file-backed iteration state (durable per-round JSON)
- 1.5 HeadroomLedger   — per-hypothesis improvement headroom and budget guard
- 1.6 AnalysisLayer    — static attribution (parameter -> QoR sensitivity) and
                         dynamic accumulation (per-round trend / stall input)

Design contract: this module is a *planning* layer. It never imports Runtime,
never launches a process, and never executes EDA itself; the only executable
handoff is a plan that the scheduler policy layer may submit.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .agent_trace import AgentTrace, AgentTraceStore

# ---------------------------------------------------------------------------
# 1.4  File-backed iteration state
# ---------------------------------------------------------------------------


@dataclass
class IterationState:
    """One durable round of the optimizer loop (written to a JSON file)."""

    round: int
    parameters: dict[str, float]
    metrics: dict[str, float] = field(default_factory=dict)
    score: float | None = None
    hypothesis_id: str | None = None
    status: str = "pending"          # pending | running | succeeded | failed | stalled
    stall_redirected: bool = False
    created_at: float = field(default_factory=time.time)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "IterationState":
        known = {field.name for field in dataclasses.fields(cls)}
        return cls(**{key: value for key, value in payload.items() if key in known})


class IterationLedger:
    """Append-only JSON-lines ledger of iteration states (file-backed, durable)."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def append(self, state: IterationState) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(state.to_dict(), ensure_ascii=False) + "\n")

    def replace_round(self, round_no: int, state: IterationState) -> None:
        """Rewrite a single round in place (used to backfill scored results)."""
        lines = self.path.read_text(encoding="utf-8").splitlines()
        payload = json.dumps(state.to_dict(), ensure_ascii=False)
        out = []
        for line in lines:
            if not line.strip():
                continue
            item = json.loads(line)
            out.append(payload if item.get("round") == round_no else line)
        if not any(json.loads(line).get("round") == round_no for line in out):
            out.append(payload)
        self.path.write_text("\n".join(out) + "\n", encoding="utf-8")

    def read(self) -> list[IterationState]:
        states = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                states.append(IterationState.from_dict(json.loads(line)))
            except (TypeError, ValueError, KeyError):
                continue
        return states

    def latest(self) -> IterationState | None:
        states = self.read()
        return states[-1] if states else None

    def clear(self) -> None:
        self.path.write_text("", encoding="utf-8")


# ---------------------------------------------------------------------------
# 1.5  Headroom ledger
# ---------------------------------------------------------------------------


@dataclass
class HeadroomEntry:
    """Expected improvement headroom for one hypothesis, with budget guard."""

    hypothesis_id: str
    metric_name: str
    direction: str                    # min | max
    baseline_value: float
    expected_value: float
    budget_remaining: int
    created_at: float = field(default_factory=time.time)

    @property
    def expected_gain(self) -> float:
        delta = self.expected_value - self.baseline_value
        return delta if self.direction == "max" else -delta

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class HeadroomLedger:
    """Tracks expected-vs-actual improvement per hypothesis and guards budget."""

    def __init__(self) -> None:
        self._entries: dict[str, HeadroomEntry] = {}

    def register(self, entry: HeadroomEntry) -> None:
        self._entries[entry.hypothesis_id] = entry

    def entry(self, hypothesis_id: str) -> HeadroomEntry | None:
        return self._entries.get(hypothesis_id)

    def budget_exhausted(self, remaining: int, *, minimum: int = 1) -> bool:
        return remaining < minimum

    def rank_remaining(self, limit: int = 5) -> list[HeadroomEntry]:
        """Hypotheses with the largest expected gain, for human review."""
        ranked = sorted(
            (entry for entry in self._entries.values()
             if entry.budget_remaining > 0),
            key=lambda item: -item.expected_gain,
        )
        return ranked[:limit]

    def to_dict(self) -> dict[str, Any]:
        return {"entries": [entry.to_dict() for entry in self._entries.values()]}


# ---------------------------------------------------------------------------
# 1.6  Static / dynamic analysis layer
# ---------------------------------------------------------------------------


@dataclass
class StaticAttribution:
    """Parameter -> QoR sensitivity from observed rounds (Pearson-like, robust)."""

    parameter: str
    metric: str
    correlation: float            # normalized [-1, 1]; NaN -> 0.0
    sample_count: int
    direction_hint: str           # lower is better | higher is better | unclear

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class DynamicTrend:
    """Accumulated per-round dynamics feeding the Disruptor."""

    metric: str
    direction: str
    values: list[float] = field(default_factory=list)
    rounds: list[int] = field(default_factory=list)

    def best_so_far(self) -> float | None:
        if not self.values:
            return None
        return (min(self.values) if self.direction == "min"
                else max(self.values))

    def improvement_over(self, window: int) -> float | None:
        """Signed improvement over the last `window` scored rounds."""
        if len(self.values) < 2:
            return None
        window = max(1, min(window, len(self.values)))
        head, tail = self.values[-window], self.values[-1]
        return (tail - head) if self.direction == "max" else (head - tail)

    def stalled(self, window: int, tolerance: float) -> bool:
        improvement = self.improvement_over(window)
        if improvement is None:
            return False
        return abs(improvement) <= tolerance

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class AnalysisLayer:
    """Static attribution + dynamic accumulation over observed iterations."""

    @staticmethod
    def _correlation(x: list[float], y: list[float]) -> float:
        if len(x) < 3 or len(x) != len(y):
            return 0.0
        mean_x = sum(x) / len(x)
        mean_y = sum(y) / len(y)
        cov = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y))
        var_x = sum((a - mean_x) ** 2 for a in x)
        var_y = sum((b - mean_y) ** 2 for b in y)
        if var_x <= 1e-12 or var_y <= 1e-12:
            return 0.0
        value = cov / math.sqrt(var_x * var_y)
        return max(-1.0, min(1.0, value))

    @classmethod
    def static_attribution(
        cls, states: Sequence[IterationState],
        parameters: Sequence[str], metric: str, direction: str,
    ) -> list[StaticAttribution]:
        scored = [state for state in states
                  if state.status == "succeeded" and state.metrics
                  and metric in state.metrics]
        result: list[StaticAttribution] = []
        for name in parameters:
            pairs = [(state.parameters[name], state.metrics[metric])
                     for state in scored if name in state.parameters]
            if not pairs:
                continue
            x = [pair[0] for pair in pairs]
            y = [pair[1] for pair in pairs]
            correlation = cls._correlation(x, y)
            hint = ("lower is better" if (direction == "min" and correlation > 0)
                    else "higher is better" if (direction == "max" and correlation > 0)
                    else "unclear")
            result.append(StaticAttribution(
                parameter=name, metric=metric, correlation=round(correlation, 4),
                sample_count=len(pairs), direction_hint=hint,
            ))
        result.sort(key=lambda item: -abs(item.correlation))
        return result

    @classmethod
    def dynamic_trend(cls, states: Sequence[IterationState], metric: str,
                      direction: str) -> DynamicTrend:
        trend = DynamicTrend(metric=metric, direction=direction)
        for state in states:
            if state.status == "succeeded" and state.metrics and metric in state.metrics:
                trend.values.append(state.metrics[metric])
                trend.rounds.append(state.round)
        return trend


# ---------------------------------------------------------------------------
# 1.1 / 1.2 / 1.3  Agent loop, stall redirection, future coder interface
# ---------------------------------------------------------------------------


@dataclass
class OptimizerHypothesis:
    """A concrete next-step proposal: what to try and why."""

    hypothesis_id: str
    rationale: str
    parameters: dict[str, float]
    metric_name: str
    direction: str                    # min | max
    expected_value: float | None = None
    source: str = "analysis"          # analysis | bo | knowledge | disruptor
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class OptimizerPlan:
    """The planning-layer handoff. Executable only by the scheduler policy."""

    plan_id: str
    hypothesis: OptimizerHypothesis
    round: int
    execution_allowed: bool = False
    required_gate: str = "human_review"
    budget_remaining: int = 0
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"plan_id": self.plan_id, "hypothesis": self.hypothesis.to_dict(),
                "round": self.round, "execution_allowed": self.execution_allowed,
                "required_gate": self.required_gate,
                "budget_remaining": self.budget_remaining,
                "created_at": self.created_at}


class OptimizerAgent:
    """Iterative loop: analyze -> hypothesize -> (execute via plan) -> score
    -> record -> lessons. Every step is written to an AgentTrace so the web
    dashboard can show the loop instead of a black box."""

    def __init__(
        self,
        ledger: IterationLedger,
        *,
        trace_store: AgentTraceStore | None = None,
        parameter_bounds: Mapping[str, tuple[float, float]] | None = None,
        metric: str = "worst_slack",
        direction: str = "min",
        max_rounds: int = 20,
        random_seed: int = 20260819,
    ):
        self.ledger = ledger
        self.trace_store = trace_store
        self.parameter_bounds = dict(parameter_bounds or {})
        self.metric = metric
        self.direction = direction
        self.max_rounds = max_rounds
        self.headroom = HeadroomLedger()
        self.analysis = AnalysisLayer()
        self._rng = random_seed
        self.lessons: list[str] = []

    # -- analysis ---------------------------------------------------------

    def analyze(self, states: Sequence[IterationState]) -> dict[str, Any]:
        parameters = list(self.parameter_bounds.keys()) or [
            name for state in states for name in state.parameters]
        attribution = self.analysis.static_attribution(
            states, parameters, self.metric, self.direction)
        trend = self.analysis.dynamic_trend(states, self.metric, self.direction)
        return {"attribution": attribution, "trend": trend}

    # -- hypothesis -------------------------------------------------------

    def hypothesize(self, states: Sequence[IterationState],
                    analysis: dict[str, Any]) -> OptimizerHypothesis:
        best = None
        for state in states:
            if state.status == "succeeded" and state.metrics and self.metric in state.metrics:
                if best is None or (
                    self.direction == "min" and state.metrics[self.metric] < best.metrics[self.metric]
                ) or (
                    self.direction == "max" and state.metrics[self.metric] > best.metrics[self.metric]
                ):
                    best = state

        params: dict[str, float] = {}
        rationale_parts: list[str] = []

        # Start from the best known point; perturb the most impactful parameter.
        if best is not None:
            params = dict(best.parameters)
        attribution = analysis.get("attribution") or []
        for item in attribution:
            if item.correlation == 0.0:
                continue
            name = item.parameter
            if name not in self.parameter_bounds:
                continue
            low, high = self.parameter_bounds[name]
            span = high - low
            current = params.get(name, (low + high) / 2.0)
            # Push opposite the direction of the correlation that hurts QoR.
            step = 0.1 * span * (1.0 if item.correlation < 0 else -1.0)
            if self.direction == "max":
                step = -step
            proposed = min(high, max(low, current + step))
            if abs(proposed - current) > 1e-9:
                params[name] = round(proposed, 4)
                rationale_parts.append(
                    f"{name} {current}->{proposed} (corr {item.correlation:+.2f})")
            break  # one targeted change per round keeps experiments interpretable

        if not params:
            # Cold start: deterministic midpoint probes.
            params = {name: round((low + high) / 2.0, 4)
                      for name, (low, high) in self.parameter_bounds.items()}
            rationale_parts.append("cold-start midpoint probe")

        expected = None
        if best is not None and best.metrics.get(self.metric) is not None:
            expected = best.metrics[self.metric]
        rationale = ("; ".join(rationale_parts)) or "initial probe"
        return OptimizerHypothesis(
            hypothesis_id=f"hyp-{self._seed()}-{uuid.uuid4().hex[:8]}",
            rationale=rationale, parameters=params,
            metric_name=self.metric, direction=self.direction,
            expected_value=expected,
        )

    def _seed(self) -> str:
        self._rng = (self._rng * 1103515245 + 12345) & 0x7FFFFFFF
        return f"{self._rng:08x}"

    # -- plan / execute handoff -------------------------------------------

    def plan(self, hypothesis: OptimizerHypothesis, round_no: int,
             budget_remaining: int) -> OptimizerPlan:
        return OptimizerPlan(
            plan_id=f"plan-{self._seed()}-{uuid.uuid4().hex[:8]}",
            hypothesis=hypothesis, round=round_no,
            execution_allowed=False, required_gate="human_review",
            budget_remaining=budget_remaining,
        )

    # -- score / record / lessons ------------------------------------------

    def score(self, state: IterationState,
              metric_value: float | None) -> tuple[float, str]:
        if metric_value is None:
            return 0.0, "no metric reported"
        # Normalize score to 0..1 against the best known value so far.
        best = None
        for prior in self.ledger.read():
            if prior.status == "succeeded" and prior.metrics.get(self.metric) is not None:
                if best is None or (
                    self.direction == "min" and prior.metrics[self.metric] < best
                ) or (
                    self.direction == "max" and prior.metrics[self.metric] > best
                ):
                    best = prior.metrics[self.metric]
        if best is None or best == metric_value:
            return 1.0, "first scored round"
        if self.direction == "min":
            improved = metric_value < best
        else:
            improved = metric_value > best
        magnitude = abs(metric_value - best) / max(1e-9, abs(best))
        score = min(1.0, magnitude) if improved else 0.0
        return round(score, 4), ("improved" if improved else "no improvement")

    def record_observation(self, round_no: int, parameters: dict[str, float],
                           metrics: dict[str, float], status: str = "succeeded",
                           notes: str = "") -> IterationState:
        state = IterationState(round=round_no, parameters=parameters,
                               metrics=metrics, status=status, notes=notes)
        score, note = self.score(state, metrics.get(self.metric))
        state.score = score
        state.notes = (notes + " | " + note).strip(" |")
        self.ledger.replace_round(round_no, state)
        if score > 0:
            self.lessons.append(
                f"round {round_no}: {self.metric} {'improved' if self.direction == 'min' else 'improved'} "
                f"({note}) params={ {k: v for k, v in parameters.items()} }"
            )
        return state

    # -- full loop ----------------------------------------------------------

    def run_iteration(self, metric_value: float | None = None,
                      budget_remaining: int = 10,
                      execute: Callable[[OptimizerPlan], dict[str, float]] | None = None,
                      trace: AgentTrace | None = None) -> dict[str, Any]:
        """One loop pass: analyze -> hypothesize -> plan -> (execute) -> record.

        `execute` is the *scheduler-side* callback; when absent the plan is
        returned for human review (the default, execution_allowed=False).
        """
        states = self.ledger.read()
        round_no = (states[-1].round + 1) if states else 1
        if round_no > self.max_rounds:
            return {"status": "budget_exhausted", "round": round_no}

        analysis = self.analyze(states)
        hypothesis = self.hypothesize(states, analysis)
        budget = max(0, budget_remaining - 1)
        plan = self.plan(hypothesis, round_no, budget)
        self.headroom.register(HeadroomEntry(
            hypothesis_id=hypothesis.hypothesis_id,
            metric_name=self.metric, direction=self.direction,
            baseline_value=(states[-1].metrics.get(self.metric)
                            if states and states[-1].metrics.get(self.metric) is not None
                            else hypothesis.expected_value or 0.0),
            expected_value=hypothesis.expected_value or 0.0,
            budget_remaining=budget,
        ))

        pending = IterationState(round=round_no, parameters=hypothesis.parameters,
                                 status="pending",
                                 hypothesis_id=hypothesis.hypothesis_id)
        self.ledger.append(pending)

        if trace is not None:
            trace.add("think", "分析历史观测",
                      detail=(f"参数敏感性: " + "; ".join(
                          f"{item.parameter}={item.correlation:+.2f}"
                          for item in analysis["attribution"][:4]) or "无历史"))
            trace.add("plan", "提出下一轮假设",
                      detail=hypothesis.rationale, metrics={"params": hypothesis.parameters})
            trace.add("tool_call", "生成可审查实验计划",
                      tool="planning-layer", status="ok",
                      detail=f"plan {plan.plan_id} round {round_no} budget {budget}",
                      metrics={"execution_allowed": plan.execution_allowed})

        metrics: dict[str, float] = {}
        if execute is not None:
            if trace is not None:
                trace.add("think", "调度策略已批准执行，提交实验")
            try:
                metrics = execute(plan) or {}
            except Exception as exc:
                metrics = {}
                self.record_observation(round_no, hypothesis.parameters,
                                        {"error": 0.0}, status="failed",
                                        notes=f"{type(exc).__name__}: {exc}")
                if trace is not None:
                    trace.add("evaluate", "执行失败", status="failed",
                              detail=str(exc)[:300])
                return {"status": "failed", "round": round_no, "plan": plan.to_dict()}
        else:
            metrics = {}
            if trace is not None:
                trace.add("result", "计划待人工审查",
                          detail=f"required gate: {plan.required_gate}",
                          metrics={"budget_remaining": budget})
            # Planning mode: the pending round stays pending until a human or
            # scheduler backfills the observation via record_observation().
            return {"status": "pending_review", "round": round_no,
                    "state": pending.to_dict(), "plan": plan.to_dict(),
                    "headroom": self.headroom.to_dict(),
                    "attribution": [item.to_dict() for item in analysis["attribution"]],
                    "trend": analysis["trend"].to_dict(),
                    "lessons": list(self.lessons[-5:])}

        recorded = self.record_observation(round_no, hypothesis.parameters,
                                           metrics or {})
        return {"status": "recorded", "round": round_no,
                "state": recorded.to_dict(), "plan": plan.to_dict(),
                "headroom": self.headroom.to_dict(),
                "attribution": [item.to_dict() for item in analysis["attribution"]],
                "trend": analysis["trend"].to_dict(),
                "lessons": list(self.lessons[-5:])}


class DisruptorAgent:
    """Stall detection + redirection.

    When the dynamic trend shows no improvement over `stall_window` scored
    rounds, the Disruptor proposes a redirect: widen exploration around the
    best point, shift objective weight, or extend the search budget.
    """

    def __init__(self, *, stall_window: int = 4, tolerance: float = 1e-6):
        self.stall_window = max(2, int(stall_window))
        self.tolerance = float(tolerance)
        self.redirect_count = 0

    def check(self, trend: DynamicTrend) -> dict[str, Any]:
        stalled = trend.stalled(self.stall_window, self.tolerance)
        best = trend.best_so_far()
        improvement = trend.improvement_over(self.stall_window)
        if not stalled:
            return {"stalled": False, "best": best,
                    "window_improvement": improvement,
                    "redirect": None}
        self.redirect_count += 1
        return {
            "stalled": True, "best": best,
            "window_improvement": improvement,
            "redirect": {
                "kind": "widen_exploration",
                "detail": (f"No improvement over {self.stall_window} rounds "
                           f"(tolerance {self.tolerance}); widen exploration "
                           f"around best point {best!r}"),
                "suggested": {
                    "exploration": max(0.05, min(0.5, 0.05 * (1 + self.redirect_count))),
                    "objective_shift": self.redirect_count % 2 == 1,
                    "extend_budget": True,
                },
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {"stall_window": self.stall_window,
                "tolerance": self.tolerance,
                "redirect_count": self.redirect_count}


class CoderAgent:
    """1.3 Future source-level optimization agent (interface placeholder).

    The platform already validates code-level candidates through DPLEvolve /
    coding_agent with an isolated validation gate. This agent is the *planning*
    interface for a future Optimizer that proposes source edits: it never
    edits files itself; it emits a reviewable plan for the scheduler policy.
    """

    required_gate = "coding_agent_isolated_validation"

    def propose_edit(self, objective: str, baseline_ref: str,
                     candidates: Sequence[Mapping[str, Any]],
                     context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Return a reviewable edit plan (never executes anything).

        candidates: list of {"kind": ..., "description": ..., "risk": ...}
        """
        validated = []
        for candidate in candidates:
            kind = candidate.get("kind", "unknown")
            description = str(candidate.get("description") or "")[:2000]
            risk = candidate.get("risk", "medium")
            if not description.strip():
                continue
            validated.append({
                "candidate_id": f"code-{hashlib.sha256(description.encode()).hexdigest()[:12]}",
                "kind": kind, "description": description, "risk": risk,
            })
        return {
            "agent": "coder",
            "objective": objective[:2000],
            "baseline_ref": baseline_ref,
            "context": dict(context or {}),
            "execution_allowed": False,
            "required_gate": self.required_gate,
            "candidates": validated,
            "created_at": time.time(),
        }
