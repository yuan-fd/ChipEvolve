"""Provenance read models and lineage.

Applications cannot open the kernel database, so these views are how evidence
reaches a screen.  The important assertions are the lineage ones: a metric whose
source cannot be named is not evidence, and it must be visible as such rather
than quietly averaged into a table.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from openroad_platform_contracts import (
    AttemptStatus,
    Metric,
    PluginManifest,
    RuntimeStatus,
    TaskSpec,
)
from openroad_platform_provenance import (
    EvidenceIndex,
    MAX_RUN_LIMIT,
    unsourced_metrics,
)
from openroad_platform_runtime import RuntimeStore


@pytest.fixture()
def store(tmp_path: Path) -> RuntimeStore:
    s = RuntimeStore(tmp_path / "runtime.db")
    yield s
    s.close()


def make_run(store: RuntimeStore, *, task_id: str = "task-1",
             project_id: str = "proj", design_id: str = "gcd",
             plugin_id: str = "some-capability"):
    return store.submit_run(
        TaskSpec(task_id=task_id, project_id=project_id, design_id=design_id,
                 plugin_id=plugin_id),
        stage_key="main", plugin_version="1.0.0",
    )


def populate(store: RuntimeStore, workspace: Path, *, run_id: str,
             with_metric: bool = True):
    """Produce an attempt with an artifact, and optionally a metric citing it."""
    workspace.mkdir(parents=True, exist_ok=True)
    stage = store.list_stages(run_id)[0]
    attempt = store.start_attempt(stage.stage_run_id, worker_id="w1",
                                  workspace=workspace, lease_seconds=30)
    (workspace / "report.json").write_text('{"area_um2": 42.0}', encoding="utf-8")
    artifact_ids = store.register_artifacts(
        attempt.attempt_id, workspace,
        [{"kind": "report", "store_key": "report.json"}],
    )
    if with_metric:
        store.register_metrics(attempt.attempt_id, [
            Metric(name="area_um2", value=42.0, unit="um2",
                   source_artifact_id=artifact_ids[0],
                   parser_id="fixture", parser_version="1"),
        ])
    store.record_event(run_id, "stage.started", {"stage": "anything"},
                       producer="runtime", stage_run_id=stage.stage_run_id,
                       attempt_id=attempt.attempt_id)
    store.finish_attempt(attempt.attempt_id, AttemptStatus.SUCCEEDED, exit_code=0)
    store.transition_run(run_id, RuntimeStatus.SUCCEEDED)
    return attempt, artifact_ids


# --------------------------------------------------------------------------
# run listing
# --------------------------------------------------------------------------

def test_runs_are_listed_newest_first_with_counts(store, tmp_path):
    first = make_run(store, task_id="task-1")
    populate(store, tmp_path / "ws1", run_id=first.run_id)
    second = make_run(store, task_id="task-2")

    index = EvidenceIndex(store)
    summaries = index.runs()
    assert [s.task_id for s in summaries] == ["task-2", "task-1"]
    by_id = {s.run_id: s for s in summaries}
    assert by_id[first.run_id].artifacts == 1
    assert by_id[first.run_id].metrics == 1
    assert by_id[first.run_id].attempts == 1
    assert by_id[second.run_id].artifacts == 0
    # The serialised form groups them, which is what a client consumes.
    assert by_id[first.run_id].to_dict()["counts"] == {
        "attempts": 1, "artifacts": 1, "metrics": 1,
    }


def test_runs_can_be_filtered(store, tmp_path):
    a = make_run(store, task_id="a", project_id="p1", design_id="gcd",
                 plugin_id="cap-a")
    b = make_run(store, task_id="b", project_id="p2", design_id="uart",
                 plugin_id="cap-b")
    index = EvidenceIndex(store)
    assert {s.task_id for s in index.runs(project_id="p1")} == {"a"}
    assert {s.task_id for s in index.runs(design_id="uart")} == {"b"}
    assert {s.task_id for s in index.runs(plugin_id="cap-a")} == {"a"}
    assert index.runs(project_id="absent") == []


def test_runs_can_be_filtered_by_status(store):
    make_run(store, task_id="queued")
    index = EvidenceIndex(store)
    assert {s.task_id for s in index.runs(status="queued")} == {"queued"}
    assert index.runs(status="succeeded") == []


def test_an_unknown_status_is_refused(store):
    with pytest.raises(ValueError):
        EvidenceIndex(store).runs(status="not-a-status")


def test_the_listing_limit_is_bounded(store):
    index = EvidenceIndex(store)
    with pytest.raises(ValueError, match="limit must be between"):
        index.runs(limit=0)
    with pytest.raises(ValueError, match="limit must be between"):
        index.runs(limit=MAX_RUN_LIMIT + 1)


def test_the_limit_is_applied(store):
    for i in range(5):
        make_run(store, task_id=f"t{i}")
    assert len(EvidenceIndex(store).runs(limit=2)) == 2


# --------------------------------------------------------------------------
# lineage
# --------------------------------------------------------------------------

def test_a_metric_carries_its_full_chain(store, tmp_path):
    run = make_run(store)
    populate(store, tmp_path / "ws", run_id=run.run_id)
    metrics = EvidenceIndex(store).metrics(run.run_id)
    assert len(metrics) == 1
    entry = metrics[0]
    assert entry.name == "area_um2"
    assert entry.value == 42.0
    assert entry.run_id == run.run_id
    assert entry.artifact_id is not None
    assert entry.artifact_kind == "report"
    assert len(entry.artifact_sha256) == 64
    assert entry.parser_id == "fixture"
    assert entry.to_dict()["complete"] is True


def test_the_recorded_hash_is_the_one_computed_from_the_bytes(store, tmp_path):
    """The chain is only worth having if the hash in it is the real one."""
    run = make_run(store)
    workspace = tmp_path / "ws"
    populate(store, workspace, run_id=run.run_id)
    entry = EvidenceIndex(store).metrics(run.run_id)[0]
    expected = hashlib.sha256((workspace / "report.json").read_bytes()).hexdigest()
    assert entry.artifact_sha256 == expected


def test_an_unsourced_metric_is_visible_rather_than_dropped(store, tmp_path):
    """A number with no source is not evidence; hiding it would be worse than
    showing it, because the store still contains it."""
    run = make_run(store)
    stage = store.list_stages(run.run_id)[0]
    attempt = store.start_attempt(stage.stage_run_id, worker_id="w1",
                                  workspace=tmp_path, lease_seconds=30)
    store.register_metrics(attempt.attempt_id, [Metric(name="orphan", value=1.0)])

    index = EvidenceIndex(store)
    metrics = index.metrics(run.run_id)
    assert [m.name for m in metrics] == ["orphan"]
    assert metrics[0].artifact_id is None
    assert metrics[0].to_dict()["complete"] is False
    assert [m.name for m in unsourced_metrics(index, run.run_id)] == ["orphan"]


def test_complete_only_filters_the_unsourced(store, tmp_path):
    run = make_run(store)
    populate(store, tmp_path / "ws", run_id=run.run_id)
    stage = store.list_stages(run.run_id)[0]
    attempt = store.list_attempts(stage.stage_run_id)[0]
    store.register_metrics(attempt.attempt_id, [Metric(name="orphan", value=1.0)])

    index = EvidenceIndex(store)
    assert {m.name for m in index.metrics(run.run_id)} == {"area_um2", "orphan"}
    assert {m.name for m in index.metrics(run.run_id, complete_only=True)} == {
        "area_um2"
    }


def test_a_run_with_no_metrics_is_an_empty_list_not_an_error(store):
    run = make_run(store)
    assert EvidenceIndex(store).metrics(run.run_id) == []


# --------------------------------------------------------------------------
# artifacts
# --------------------------------------------------------------------------

def test_artifacts_are_flattened_with_their_attempt(store, tmp_path):
    run = make_run(store)
    attempt, artifact_ids = populate(store, tmp_path / "ws", run_id=run.run_id)
    artifacts = EvidenceIndex(store).artifacts(run.run_id)
    assert len(artifacts) == 1
    assert artifacts[0]["artifact_id"] == artifact_ids[0]
    assert artifacts[0]["attempt_id"] == attempt.attempt_id
    assert artifacts[0]["stage_key"] == "main"
    assert artifacts[0]["run_id"] == run.run_id


def test_finding_an_artifact_by_id(store, tmp_path):
    run = make_run(store)
    _, artifact_ids = populate(store, tmp_path / "ws", run_id=run.run_id)
    index = EvidenceIndex(store)
    found = index.find_artifact(run.run_id, artifact_ids[0])
    assert found is not None and found["kind"] == "report"
    assert index.find_artifact(run.run_id, "art-absent") is None


# --------------------------------------------------------------------------
# timeline
# --------------------------------------------------------------------------

def test_the_timeline_is_ordered_and_names_its_producer(store, tmp_path):
    run = make_run(store)
    stage = store.list_stages(run.run_id)[0]
    attempt = store.start_attempt(stage.stage_run_id, worker_id="w1",
                                  workspace=tmp_path, lease_seconds=30)
    for event_type in ("stage.started", "stage.finished"):
        store.record_event(run.run_id, event_type, {"stage": "alpha"},
                           producer="adapter:x@1", stage_run_id=stage.stage_run_id,
                           attempt_id=attempt.attempt_id)
    timeline = EvidenceIndex(store).timeline(run.run_id)
    assert [e["event_type"] for e in timeline] == ["stage.started", "stage.finished"]
    assert all(e["producer"] == "adapter:x@1" for e in timeline)
    assert timeline[0]["payload"] == {"stage": "alpha"}


# --------------------------------------------------------------------------
# the graph
# --------------------------------------------------------------------------

def test_the_graph_types_every_node_and_names_every_edge(store, tmp_path):
    run = make_run(store)
    populate(store, tmp_path / "ws", run_id=run.run_id)
    graph = EvidenceIndex(store).artifact_graph([run.run_id])

    counts = graph.counts()
    assert counts == {"run": 1, "stage": 1, "attempt": 1,
                      "artifact": 1, "metric": 1}

    edge_kinds = {e["kind"] for e in graph.edges}
    assert edge_kinds == {"has_stage", "has_attempt", "produced", "sources",
                          "measured"}

    # Every edge points at a node that exists, so a renderer cannot dangle.
    node_ids = {n["id"] for n in graph.nodes}
    for edge in graph.edges:
        assert edge["from"] in node_ids
        assert edge["to"] in node_ids


def test_the_graph_links_a_metric_to_its_artifact(store, tmp_path):
    run = make_run(store)
    populate(store, tmp_path / "ws", run_id=run.run_id)
    graph = EvidenceIndex(store).artifact_graph([run.run_id])
    sources = [e for e in graph.edges if e["kind"] == "sources"]
    assert len(sources) == 1
    assert sources[0]["from"].startswith("artifact:")
    assert sources[0]["to"].startswith("metric:")
    assert sources[0]["to"].endswith(":area_um2")


def test_an_unsourced_metric_is_marked_in_the_graph(store, tmp_path):
    run = make_run(store)
    stage = store.list_stages(run.run_id)[0]
    attempt = store.start_attempt(stage.stage_run_id, worker_id="w1",
                                  workspace=tmp_path, lease_seconds=30)
    store.register_metrics(attempt.attempt_id, [Metric(name="orphan", value=1.0)])
    graph = EvidenceIndex(store).artifact_graph([run.run_id])
    metric_node = next(n for n in graph.nodes if n["kind"] == "metric")
    assert metric_node["sourced"] is False
    # No sources edge exists, because there is nothing to point at.
    assert not [e for e in graph.edges if e["kind"] == "sources"]


def test_the_graph_covers_several_runs(store, tmp_path):
    first = make_run(store, task_id="a")
    second = make_run(store, task_id="b")
    populate(store, tmp_path / "ws1", run_id=first.run_id)
    populate(store, tmp_path / "ws2", run_id=second.run_id)
    graph = EvidenceIndex(store).artifact_graph([first.run_id, second.run_id])
    assert graph.counts()["run"] == 2
    run_nodes = {n["label"] for n in graph.nodes if n["kind"] == "run"}
    assert run_nodes == {"a", "b"}


def test_an_empty_graph_is_not_an_error(store):
    graph = EvidenceIndex(store).artifact_graph([])
    assert graph.nodes == [] and graph.edges == []
    assert graph.counts() == {}


# --------------------------------------------------------------------------
# the read model is strictly a read
# --------------------------------------------------------------------------

def test_reading_does_not_modify_the_store(store, tmp_path):
    """The runtime is the only writer; a view that mutates would break that."""
    run = make_run(store)
    populate(store, tmp_path / "ws", run_id=run.run_id)
    index = EvidenceIndex(store)

    before = store.describe_run(run.run_id)
    index.runs()
    detail = index.run_detail(run.run_id)
    assert (detail["project_id"], detail["design_id"]) == ("proj", "gcd")
    index.metrics(run.run_id)
    index.artifacts(run.run_id)
    index.timeline(run.run_id)
    index.artifact_graph([run.run_id])
    assert store.describe_run(run.run_id) == before
