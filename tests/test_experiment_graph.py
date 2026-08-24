from __future__ import annotations

import pytest

from openroad_platform_contracts import (
    ActionKind,
    ActionSpec,
    ExperimentEdge,
    ExperimentNode,
    ExperimentNodeKind,
)
from openroad_platform_scheduler import ExperimentGraphStore


def node(node_id: str, kind: ExperimentNodeKind) -> ExperimentNode:
    return ExperimentNode(
        node_id=node_id, experiment_id="exp-v2", kind=kind,
        producer="test", payload={"version": 1},
        evidence_refs=("run:baseline",), created_at="2026-08-23T00:00:00Z",
    )


def test_experiment_graph_is_append_only_and_enforces_four_gate_order(tmp_path):
    store = ExperimentGraphStore(tmp_path / "graph.db")
    kinds = [
        ExperimentNodeKind.DESIGN_REVISION, ExperimentNodeKind.BASELINE,
        ExperimentNodeKind.OBSERVATION, ExperimentNodeKind.DIAGNOSIS,
        ExperimentNodeKind.PROPOSAL, ExperimentNodeKind.REVIEW,
        ExperimentNodeKind.ATTEMPT, ExperimentNodeKind.MEASUREMENT,
        ExperimentNodeKind.DECISION, ExperimentNodeKind.MEMORY,
    ]
    for index, kind in enumerate(kinds):
        store.append_node(node(f"node-{index}", kind))
    for index in range(len(kinds) - 1):
        store.append_edge(ExperimentEdge(
            experiment_id="exp-v2", parent_node_id=f"node-{index}",
            child_node_id=f"node-{index + 1}", relation="leads_to",
        ))
    graph = store.describe("exp-v2")
    assert [item["kind"] for item in graph["nodes"]] == [item.value for item in kinds]
    assert len(graph["edges"]) == len(kinds) - 1
    with pytest.raises(ValueError, match="immutable"):
        store.append_node(node("node-0", ExperimentNodeKind.DESIGN_REVISION))


def test_experiment_graph_rejects_skipped_review_and_cycles(tmp_path):
    store = ExperimentGraphStore(tmp_path / "graph.db")
    for item in (node("proposal", ExperimentNodeKind.PROPOSAL),
                 node("attempt", ExperimentNodeKind.ATTEMPT),
                 node("review", ExperimentNodeKind.REVIEW)):
        store.append_node(item)
    with pytest.raises(ValueError, match="Invalid Experiment Graph transition"):
        store.append_edge(ExperimentEdge(
            experiment_id="exp-v2", parent_node_id="proposal", child_node_id="attempt",
            relation="bypasses_review",
        ))
    store.append_edge(ExperimentEdge(
        experiment_id="exp-v2", parent_node_id="proposal", child_node_id="review",
        relation="reviewed_by",
    ))
    store.append_edge(ExperimentEdge(
        experiment_id="exp-v2", parent_node_id="review", child_node_id="attempt",
        relation="permits",
    ))
    with pytest.raises(ValueError, match="Invalid Experiment Graph transition|cycle"):
        store.append_edge(ExperimentEdge(
            experiment_id="exp-v2", parent_node_id="attempt", child_node_id="proposal",
            relation="loops",
        ))


def test_action_spec_requires_evidence_and_never_carries_shell_material():
    action = ActionSpec(
        action_id="act-1", experiment_id="exp-v2", proposal_node_id="proposal-1",
        kind=ActionKind.PARAMETER, hypothesis="Lower density reduces congestion.",
        expected_outcome="Route overflow decreases without timing regression.",
        stop_condition="Stop after two non-improving attempts.", rollback="Restore baseline parameters.",
        parameters={"values": {"place_density": 0.62}}, evidence_refs=("run:baseline",),
        reviewed_by="reviewer",
    )
    assert action.to_dict()["kind"] == "parameter"
    with pytest.raises(ValueError, match="executable or secret"):
        ActionSpec(
            action_id="act-2", experiment_id="exp-v2", proposal_node_id="proposal-1",
            kind=ActionKind.PARAMETER, hypothesis="x", expected_outcome="y",
            stop_condition="z", rollback="r",
            parameters={"command": "make finish"}, evidence_refs=("run:baseline",),
            reviewed_by="reviewer",
        ).validate()
    with pytest.raises(ValueError, match="patch-registry"):
        ActionSpec(
            action_id="act-3", experiment_id="exp-v2", proposal_node_id="proposal-1",
            kind=ActionKind.TOOL_CODE, hypothesis="x", expected_outcome="y",
            stop_condition="z", rollback="r",
            parameters={"patch_ref": "artifact:external:patch", "patch_surface": "tools/**"},
            evidence_refs=("run:baseline",), reviewed_by="reviewer",
        ).validate()
