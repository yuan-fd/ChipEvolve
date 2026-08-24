"""Four-gate controller: observe -> propose -> execute -> learn.

The controller owns graph projection only. WorkflowRuntime remains the only
task-state authority and a human/reviewer must supply the approved ActionSpec.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from openroad_platform_contracts import ActionKind, ActionSpec, ExperimentEdge, ExperimentNode, ExperimentNodeKind, TaskSpec

from .experiment_graph import ExperimentGraphStore
from .runtime import WorkflowRuntime


def _now() -> str: return datetime.now(timezone.utc).isoformat()
def _id(prefix: str) -> str: return f"{prefix}-{uuid.uuid4().hex}"
def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class FourGateController:
    def __init__(self, graph: ExperimentGraphStore, runtime: WorkflowRuntime):
        self.graph, self.runtime = graph, runtime

    def begin_baseline(self, task: TaskSpec, *, producer: str = "user") -> tuple[str, str]:
        task.validate(); experiment_id = _id("experiment")
        design = ExperimentNode(_id("design"), experiment_id, ExperimentNodeKind.DESIGN_REVISION,
                                producer, {"design_id": task.design_id, "task_sha256": _digest(task.to_dict())}, (), _now())
        baseline = ExperimentNode(_id("baseline"), experiment_id, ExperimentNodeKind.BASELINE,
                                  producer, {"task": task.to_dict()}, (), _now())
        self.graph.append_node(design); self.graph.append_node(baseline)
        self.graph.append_edge(ExperimentEdge(experiment_id, design.node_id, baseline.node_id, "defines_baseline"))
        run = self.runtime.submit(task)
        return experiment_id, run.run_id

    def observe_baseline(self, experiment_id: str, baseline_run_id: str, *, producer: str = "runtime") -> str:
        view = self.runtime.describe(baseline_run_id); run = view["run"]
        if run["status"] not in {"succeeded", "failed", "cancelled", "timed_out"}:
            raise ValueError("baseline Runtime run is not terminal")
        graph = self.graph.describe(experiment_id)
        baselines = [node for node in graph["nodes"] if node["kind"] == "baseline"]
        if len(baselines) != 1: raise ValueError("experiment must have exactly one baseline")
        observation = ExperimentNode(_id("observation"), experiment_id, ExperimentNodeKind.OBSERVATION,
                                     producer, {"run_id": baseline_run_id, "status": run["status"]},
                                     (f"run:{baseline_run_id}",), _now())
        self.graph.append_node(observation)
        self.graph.append_edge(ExperimentEdge(experiment_id, baselines[0]["node_id"], observation.node_id, "observed"))
        return observation.node_id

    def propose(self, experiment_id: str, observation_node_id: str, *, producer: str,
                payload: dict[str, Any], evidence_refs: tuple[str, ...]) -> str:
        node = ExperimentNode(_id("proposal"), experiment_id, ExperimentNodeKind.PROPOSAL,
                              producer, payload, evidence_refs, _now())
        self.graph.append_node(node)
        self.graph.append_edge(ExperimentEdge(experiment_id, observation_node_id, node.node_id, "supports"))
        return node.node_id

    def review_and_submit(self, action: ActionSpec, base_task: TaskSpec) -> tuple[str, str]:
        action.validate(); base_task.validate()
        if action.kind is not ActionKind.PARAMETER:
            raise ValueError("v2 runtime submission currently accepts only reviewed parameter ActionSpec")
        graph = self.graph.describe(action.experiment_id)
        proposal = next((item for item in graph["nodes"] if item["node_id"] == action.proposal_node_id), None)
        if proposal is None or proposal["kind"] != "proposal":
            raise ValueError("ActionSpec must reference a proposal in its experiment")
        values = dict(action.parameters["values"])
        if set(values) - set(base_task.parameters):
            raise ValueError("ActionSpec may adjust only declared TaskSpec parameters")
        review = ExperimentNode(_id("review"), action.experiment_id, ExperimentNodeKind.REVIEW,
                                action.reviewed_by, {"action": action.to_dict(), "approved": True},
                                action.evidence_refs, _now())
        self.graph.append_node(review)
        self.graph.append_edge(ExperimentEdge(action.experiment_id, action.proposal_node_id, review.node_id, "approved"))
        task = TaskSpec.from_dict({**base_task.to_dict(), "task_id": _id("action-task"),
                                   "parameters": {**base_task.parameters, **values},
                                   "labels": {**base_task.labels, "experiment_id": action.experiment_id,
                                              "action_id": action.action_id}})
        run = self.runtime.submit(task)
        attempt = ExperimentNode(_id("attempt"), action.experiment_id, ExperimentNodeKind.ATTEMPT,
                                 "runtime", {"run_id": run.run_id, "action_id": action.action_id},
                                 (f"run:{run.run_id}",), _now())
        self.graph.append_node(attempt)
        self.graph.append_edge(ExperimentEdge(action.experiment_id, review.node_id, attempt.node_id, "executes"))
        return run.run_id, attempt.node_id

    def observe_attempt(self, experiment_id: str, attempt_node_id: str, *, producer: str = "runtime") -> str:
        graph = self.graph.describe(experiment_id)
        attempt = next((item for item in graph["nodes"] if item["node_id"] == attempt_node_id), None)
        if attempt is None or attempt["kind"] != "attempt": raise ValueError("unknown attempt node")
        run_id = attempt["payload"]["run_id"]; view = self.runtime.describe(run_id)
        if view["run"]["status"] not in {"succeeded", "failed", "cancelled", "timed_out"}:
            raise ValueError("attempt Runtime run is not terminal")
        measurement = ExperimentNode(_id("measurement"), experiment_id, ExperimentNodeKind.MEASUREMENT,
                                    producer, {"run_id": run_id, "status": view["run"]["status"]},
                                    (f"run:{run_id}",), _now())
        self.graph.append_node(measurement)
        self.graph.append_edge(ExperimentEdge(experiment_id, attempt_node_id, measurement.node_id, "measured"))
        return measurement.node_id

    def decide_and_record_memory(self, experiment_id: str, measurement_node_id: str, *,
                                 producer: str, outcome: str, rationale: str,
                                 memory_kind: str, evidence_refs: tuple[str, ...]) -> tuple[str, str]:
        """Record a reviewed decision and an evidence-bound memory item.

        This deliberately does not alter Runtime state or claim a causal
        improvement. ``outcome`` labels the measured result, including
        negative/rejected/no_improvement cases, so retrieval can avoid
        survivor bias.
        """
        if outcome not in {"promoted", "negative", "rejected", "no_improvement"}:
            raise ValueError("outcome must be promoted, negative, rejected, or no_improvement")
        if memory_kind not in {"episodic", "semantic", "procedural", "statistical"}:
            raise ValueError("memory_kind is invalid")
        if not rationale.strip() or len(rationale) > 4000:
            raise ValueError("rationale is required and bounded")
        if not evidence_refs:
            raise ValueError("decision requires durable evidence references")
        graph = self.graph.describe(experiment_id)
        measurement = next((item for item in graph["nodes"]
                            if item["node_id"] == measurement_node_id), None)
        if measurement is None or measurement["kind"] != "measurement":
            raise ValueError("decision must reference a measurement in its experiment")
        decision = ExperimentNode(
            _id("decision"), experiment_id, ExperimentNodeKind.DECISION, producer,
            {"outcome": outcome, "rationale": rationale}, evidence_refs, _now(),
        )
        self.graph.append_node(decision)
        self.graph.append_edge(ExperimentEdge(experiment_id, measurement_node_id,
                                              decision.node_id, "decides"))
        memory = ExperimentNode(
            _id("memory"), experiment_id, ExperimentNodeKind.MEMORY, producer,
            {"memory_kind": memory_kind, "outcome": outcome,
             "rationale": rationale, "decision_node_id": decision.node_id},
            evidence_refs, _now(),
        )
        self.graph.append_node(memory)
        self.graph.append_edge(ExperimentEdge(experiment_id, decision.node_id,
                                              memory.node_id, "learns"))
        return decision.node_id, memory.node_id
