"""Policy bridge from data-only optimizer plans to authoritative Campaign/Runtime."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Mapping

from openroad_platform_contracts import ExperimentPlan, LearningContext, TaskSpec

from .campaign import GRID_PARAMETERS, TOOL_STAGES, StageAwareCampaignManager


def _digest(payload) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


class OptimizationCampaignBridge:
    """Translate an approved plan into tasks; never execute optimizer code."""

    def __init__(self, manager: StageAwareCampaignManager):
        self.manager = manager

    def create(self, name: str, base_task: TaskSpec, plan: ExperimentPlan, *,
               max_parallel: int = 1, stage_budgets: Mapping[str, float] | None = None,
               objective_metric: str | None = None, direction: str = "min",
               top_k: int = 1, max_repairs: int = 1) -> str:
        base_task.validate()
        plan.validate()
        if base_task.design_id != plan.design_id:
            raise ValueError("ExperimentPlan design does not match base TaskSpec")
        if plan.provenance.get("predictions_are_canonical_metrics") is not False:
            raise ValueError("Optimizer plan must explicitly isolate predicted metrics")
        if direction not in {"min", "max"} or not 1 <= top_k <= 20:
            raise ValueError("Invalid objective direction or Top-K")
        budgets = {str(stage): float(seconds) for stage, seconds in (stage_budgets or {}).items()}
        if any(stage not in TOOL_STAGES or seconds <= 0 or seconds > 86_400
               for stage, seconds in budgets.items()):
            raise ValueError("Invalid per-stage wall-clock budget")
        tasks = []
        for ordinal, candidate in enumerate(plan.candidates, 1):
            unknown = sorted(set(candidate.parameters) - set(GRID_PARAMETERS))
            if unknown:
                raise ValueError(f"Optimizer proposed unsupported parameters: {', '.join(unknown)}")
            for parameter, value in candidate.parameters.items():
                low, high = GRID_PARAMETERS[parameter]
                if isinstance(value, bool) or not isinstance(value, (int, float)) \
                        or not low <= float(value) <= high:
                    raise ValueError(f"Optimizer parameter {parameter} is outside policy")
            task_seed = _digest({"plan_id": plan.plan_id,
                                 "candidate_id": candidate.candidate_id})[:24]
            task = dataclasses.replace(
                base_task, task_id=f"optimizer-task-{task_seed}",
                parameters={**base_task.parameters, **candidate.parameters},
                labels={**base_task.labels, "candidate_ordinal": str(ordinal),
                        "optimizer_plan_id": plan.plan_id,
                        "optimizer_candidate_id": candidate.candidate_id,
                        "prediction_source": "predicted-not-canonical",
                        "repair_depth": "0"},
            )
            task.validate()
            tasks.append(task)
        campaign_id = f"optimization-{_digest({'plan_id': plan.plan_id})[:24]}"
        try:
            existing = self.manager.store.get(campaign_id)
        except KeyError:
            existing = None
        if existing is not None:
            current = self.manager.store.members(campaign_id)
            if [item.task_spec.to_dict() for item in current] != [item.to_dict() for item in tasks]:
                raise ValueError("Optimization campaign identity conflicts with existing tasks")
            return campaign_id
        self.manager.store.create(name, tasks, max_parallel=max_parallel,
                                  campaign_id=campaign_id)
        self.manager.store.set_stage_policy(campaign_id, {
            "schema_version": 1, "stage_budgets": budgets,
            "objective_metric": objective_metric, "direction": direction,
            "top_k": top_k, "max_repairs": max_repairs,
            "max_total_runs": plan.max_child_runs + max_repairs * len(tasks),
            "pruning_policy": "stage_wall_clock_v1",
            "optimizer_plan_id": plan.plan_id,
            "optimizer_producer": plan.producer,
            "predictions_are_canonical_metrics": False,
        })
        return campaign_id

    def ingest_terminal(self, campaign_id: str, *, context: LearningContext,
                        exporter, study_store, study_id: str) -> tuple[str, ...]:
        context.validate()
        view = self.manager.describe(campaign_id)
        if view["status"] != "finished":
            raise ValueError("Optimization campaign is not terminal")
        observation_ids = []
        for member in self.manager.store.members(campaign_id):
            if member.run_id is None:
                continue
            observation = exporter.export_run(member.run_id, context)
            study_store.add_observation(study_id, observation)
            observation_ids.append(observation.observation_id)
        return tuple(observation_ids)
