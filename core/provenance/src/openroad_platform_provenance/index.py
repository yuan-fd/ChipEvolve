"""Provenance: read models and lineage over the runtime's durable state.

Applications may not open the kernel's database (G5), so this is how they see
evidence at all.  Everything here is a **read**; the runtime remains the only
writer, which is what keeps a stored QoR number trustworthy.

Two things this layer is for:

* **Answering questions about runs** without applications learning the schema.
  A run's stored view is a nested document; an app that wants "the metrics of
  the last successful attempt" should not have to walk it by hand, and should
  not be tempted to query the tables directly when that gets awkward.

* **Lineage.**  A metric without a traceable source is an assertion.  For every
  metric this module can name the artifact it was read from, the attempt that
  produced it, and the run that owns the attempt -- which is the chain a reader
  needs in order to check the number themselves.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from openroad_platform_contracts import RuntimeStatus
from openroad_platform_runtime import RuntimeStore

#: How many runs a list query returns when the caller does not say.
DEFAULT_RUN_LIMIT = 50

#: The absolute ceiling, so one caller cannot pull the whole history into
#: memory by passing a large number.
MAX_RUN_LIMIT = 500


@dataclass(frozen=True)
class MetricProvenance:
    """One metric and the chain that justifies it."""

    name: str
    value: Any
    unit: str | None
    parser_id: str | None
    parser_version: str | None
    run_id: str
    attempt_id: str
    artifact_id: str | None
    artifact_kind: str | None
    artifact_sha256: str | None
    artifact_size_bytes: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "value": self.value, "unit": self.unit,
            "parser": {"id": self.parser_id, "version": self.parser_version},
            "run_id": self.run_id, "attempt_id": self.attempt_id,
            "artifact": (
                {"artifact_id": self.artifact_id, "kind": self.artifact_kind,
                 "sha256": self.artifact_sha256,
                 "size_bytes": self.artifact_size_bytes}
                if self.artifact_id is not None else None
            ),
            "complete": self.artifact_id is not None,
        }


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    task_id: str
    status: str
    project_id: str
    design_id: str
    design_revision_id: str | None
    experiment_id: str | None
    input_manifest_sha256: str | None
    plugin_id: str | None
    created_at: str
    started_at: str | None
    ended_at: str | None
    terminal_reason: str | None
    attempts: int
    artifacts: int
    metrics: int

    def to_dict(self) -> dict[str, Any]:
        result = {
            "run_id": self.run_id, "task_id": self.task_id,
            "status": self.status, "project_id": self.project_id,
            "design_id": self.design_id, "plugin_id": self.plugin_id,
            "created_at": self.created_at, "started_at": self.started_at,
            "ended_at": self.ended_at, "terminal_reason": self.terminal_reason,
            "counts": {"attempts": self.attempts, "artifacts": self.artifacts,
                       "metrics": self.metrics},
        }
        for name in ("design_revision_id", "experiment_id", "input_manifest_sha256"):
            value = getattr(self, name)
            if value is not None:
                result[name] = value
        return result


@dataclass
class ArtifactGraph:
    """A bounded graph of runs, attempts and artifacts.

    Nodes are typed and edges are named, so a renderer does not have to infer
    the relationship from the shape of an id.
    """

    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"nodes": self.nodes, "edges": self.edges}

    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for node in self.nodes:
            result[node["kind"]] = result.get(node["kind"], 0) + 1
        return result


class EvidenceIndex:
    """Read-only views over one runtime store."""

    def __init__(self, store: RuntimeStore):
        self.store = store

    # -- runs -------------------------------------------------------------

    def runs(
        self, *, project_id: str | None = None, design_id: str | None = None,
        plugin_id: str | None = None, status: str | None = None,
        owner_run_ids: set[str] | None = None,
        limit: int = DEFAULT_RUN_LIMIT, offset: int = 0,
    ) -> list[RunSummary]:
        """Summaries, newest first.

        Filtering happens here rather than in SQL so the store's schema stays
        private to the runtime; the alternative couples every reader to the
        tables, which is what G5 exists to prevent.
        """
        if not 1 <= limit <= MAX_RUN_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_RUN_LIMIT}")
        if offset < 0:
            raise ValueError("offset must not be negative")
        wanted = RuntimeStatus(status) if status else None
        if status and wanted is None:  # pragma: no cover - RuntimeStatus is total
            raise ValueError(f"unknown status {status!r}")

        with self.store._lock:
            rows = self.store._connection.execute(
                "SELECT run_id FROM runtime_runs ORDER BY created_at DESC"
            ).fetchall()

        summaries: list[RunSummary] = []
        matched = 0
        for row in rows:
            if owner_run_ids is not None and row["run_id"] not in owner_run_ids:
                continue
            run = self.store.get_run(row["run_id"])
            if project_id and run.task_spec.project_id != project_id:
                continue
            if design_id and run.task_spec.design_id != design_id:
                continue
            if plugin_id and run.task_spec.plugin_id != plugin_id:
                continue
            if wanted is not None and run.status is not wanted:
                continue
            if matched < offset:
                matched += 1
                continue
            summaries.append(self._summary(run))
            if len(summaries) >= limit:
                break
        return summaries

    def _summary(self, run) -> RunSummary:
        attempts = artifacts = metrics = 0
        for stage in self.store.list_stages(run.run_id):
            for attempt in self.store.list_attempts(stage.stage_run_id):
                attempts += 1
                artifacts += len(self.store.list_artifacts(attempt.attempt_id))
                metrics += len(self.store.list_metrics(attempt.attempt_id))
        return RunSummary(
            run_id=run.run_id, task_id=run.task_id, status=run.status.value,
            project_id=run.task_spec.project_id,
            design_id=run.task_spec.design_id,
            design_revision_id=run.task_spec.design_revision_id,
            experiment_id=run.task_spec.experiment_id,
            input_manifest_sha256=run.task_spec.input_manifest_sha256,
            plugin_id=run.task_spec.plugin_id,
            created_at=run.created_at, started_at=run.started_at,
            ended_at=run.ended_at, terminal_reason=run.terminal_reason,
            attempts=attempts, artifacts=artifacts, metrics=metrics,
        )

    def run_detail(self, run_id: str) -> dict[str, Any]:
        return self.store.describe_run(run_id)

    # -- metrics with their lineage --------------------------------------

    def metrics(self, run_id: str, *, complete_only: bool = False
                ) -> list[MetricProvenance]:
        """Every metric of a run, each tied to the artifact it came from.

        A metric whose source artifact cannot be resolved is still returned,
        with ``complete`` False.  Dropping it silently would hide the fact that
        something unsourced is in the store; the flag makes it visible.
        """
        rows: list[MetricProvenance] = []
        for attempt, artifact_index in self._attempts_of(run_id):
            for metric in self.store.list_metrics(attempt.attempt_id):
                artifact = artifact_index.get(metric.source_artifact_id or "")
                entry = MetricProvenance(
                    name=metric.name, value=metric.value, unit=metric.unit,
                    parser_id=metric.parser_id,
                    parser_version=metric.parser_version,
                    run_id=run_id, attempt_id=attempt.attempt_id,
                    artifact_id=artifact["artifact_id"] if artifact else None,
                    artifact_kind=artifact["kind"] if artifact else None,
                    artifact_sha256=artifact["sha256"] if artifact else None,
                    artifact_size_bytes=artifact["size_bytes"] if artifact else None,
                )
                if complete_only and not entry.artifact_id:
                    continue
                rows.append(entry)
        return rows

    def _attempts_of(self, run_id: str):
        view = self.store.describe_run(run_id)
        for stage in view["stages"]:
            for attempt_view in stage["attempts"]:
                index = {a["artifact_id"]: a for a in attempt_view["artifacts"]}
                attempts = [
                    a for a in self.store.list_attempts(stage["stage_run_id"])
                    if a.attempt_id == attempt_view["attempt_id"]
                ]
                yield attempts[0], index

    def artifacts(self, run_id: str) -> list[dict[str, Any]]:
        """Every registered artifact of a run, flattened, with its attempt."""
        out: list[dict[str, Any]] = []
        view = self.store.describe_run(run_id)
        for stage in view["stages"]:
            for attempt in stage["attempts"]:
                for artifact in attempt["artifacts"]:
                    out.append({
                        **artifact,
                        "run_id": run_id,
                        "stage_key": stage["stage_key"],
                        "attempt_id": attempt["attempt_id"],
                    })
        return out

    def find_artifact(self, run_id: str, artifact_id: str) -> dict[str, Any] | None:
        return next(
            (a for a in self.artifacts(run_id) if a["artifact_id"] == artifact_id),
            None,
        )

    # -- events -----------------------------------------------------------

    def timeline(self, run_id: str) -> list[dict[str, Any]]:
        """The run's events in order -- what happened, produced by whom."""
        return [
            {"event_type": e.event_type, "occurred_at": e.occurred_at,
             "producer": e.producer, "payload": e.payload,
             "stage_run_id": e.stage_run_id, "attempt_id": e.attempt_id}
            for e in self.store.list_events(run_id)
        ]

    # -- the graph --------------------------------------------------------

    def artifact_graph(self, run_ids: Iterable[str]) -> ArtifactGraph:
        """Runs, stages, attempts and artifacts, with named edges.

        Bounded by the caller's ``run_ids``: this is a view for a reader, not a
        crawl of the whole store.
        """
        graph = ArtifactGraph()
        wanted: Sequence[str] = list(run_ids)
        for run_id in wanted:
            view = self.store.describe_run(run_id)
            graph.nodes.append({
                "id": f"run:{run_id}", "kind": "run",
                "label": view["task_id"], "status": view["status"],
            })
            for stage in view["stages"]:
                stage_node = f"stage:{stage['stage_run_id']}"
                graph.nodes.append({
                    "id": stage_node, "kind": "stage",
                    "label": stage["stage_key"], "status": stage["status"],
                    "plugin_id": stage["plugin_id"],
                    "plugin_version": stage["plugin_version"],
                })
                graph.edges.append({
                    "from": f"run:{run_id}", "to": stage_node,
                    "kind": "has_stage",
                })
                for attempt in stage["attempts"]:
                    attempt_node = f"attempt:{attempt['attempt_id']}"
                    graph.nodes.append({
                        "id": attempt_node, "kind": "attempt",
                        "label": f"attempt {attempt['attempt_number']}",
                        "status": attempt["status"],
                        "worker_id": attempt["worker_id"],
                    })
                    graph.edges.append({
                        "from": stage_node, "to": attempt_node,
                        "kind": "has_attempt",
                    })
                    for artifact in attempt["artifacts"]:
                        artifact_node = f"artifact:{artifact['artifact_id']}"
                        graph.nodes.append({
                            "id": artifact_node, "kind": "artifact",
                            "label": artifact["store_key"],
                            "artifact_kind": artifact["kind"],
                            "sha256": artifact["sha256"],
                            "size_bytes": artifact["size_bytes"],
                        })
                        graph.edges.append({
                            "from": attempt_node, "to": artifact_node,
                            "kind": "produced",
                        })
                        for metric in attempt["metrics"]:
                            if metric.get("source_artifact_id") != artifact["artifact_id"]:
                                continue
                            graph.edges.append({
                                "from": artifact_node,
                                "to": f"metric:{attempt['attempt_id']}:{metric['name']}",
                                "kind": "sources",
                            })
                    for metric in attempt["metrics"]:
                        metric_node = (f"metric:{attempt['attempt_id']}:"
                                       f"{metric['name']}")
                        graph.nodes.append({
                            "id": metric_node, "kind": "metric",
                            "label": metric["name"], "value": metric["value"],
                            "unit": metric.get("unit"),
                            "sourced": metric.get("source_artifact_id") is not None,
                        })
                        graph.edges.append({
                            "from": attempt_node, "to": metric_node,
                            "kind": "measured",
                        })
        return graph


def unsourced_metrics(index: EvidenceIndex, run_id: str) -> list[MetricProvenance]:
    """Metrics in a run that cannot be traced to an artifact.

    This is the query a reviewer runs.  An unsourced metric is not necessarily
    wrong, but it is not evidence either, and it should be visible rather than
    buried.
    """
    return [m for m in index.metrics(run_id) if not m.artifact_id]
