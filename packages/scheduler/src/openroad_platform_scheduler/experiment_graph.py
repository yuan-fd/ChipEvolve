"""SQLite-backed, append-only Experiment Graph store for v2.

This store deliberately does not import Runtime or any execution adapter.  It
records why an execution was permitted and later links the immutable Runtime
attempt back into the research record.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from openroad_platform_contracts import ExperimentEdge, ExperimentNode, ExperimentNodeKind


class ExperimentGraphStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS experiment_nodes_v1 (
                    experiment_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (experiment_id, node_id)
                );
                CREATE TABLE IF NOT EXISTS experiment_edges_v1 (
                    experiment_id TEXT NOT NULL,
                    parent_node_id TEXT NOT NULL,
                    child_node_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    PRIMARY KEY (experiment_id, parent_node_id, child_node_id, relation),
                    FOREIGN KEY (experiment_id, parent_node_id)
                      REFERENCES experiment_nodes_v1(experiment_id, node_id),
                    FOREIGN KEY (experiment_id, child_node_id)
                      REFERENCES experiment_nodes_v1(experiment_id, node_id)
                );
                CREATE INDEX IF NOT EXISTS idx_experiment_edges_child
                  ON experiment_edges_v1(experiment_id, child_node_id);
            """)

    def append_node(self, node: ExperimentNode) -> None:
        node.validate()
        payload = json.dumps(node.to_dict(), ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO experiment_nodes_v1
                       (experiment_id, node_id, kind, payload_json, payload_sha256, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (node.experiment_id, node.node_id, node.kind.value, payload, digest,
                     node.created_at),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Experiment node already exists; graph nodes are immutable") from exc

    def append_edge(self, edge: ExperimentEdge) -> None:
        edge.validate()
        with self._connect() as connection:
            nodes = connection.execute(
                """SELECT node_id, kind FROM experiment_nodes_v1
                   WHERE experiment_id = ? AND node_id IN (?, ?)""",
                (edge.experiment_id, edge.parent_node_id, edge.child_node_id),
            ).fetchall()
            if len(nodes) != 2:
                raise KeyError("ExperimentEdge endpoints must exist in the same experiment")
            kinds = {row["node_id"]: ExperimentNodeKind(row["kind"]) for row in nodes}
            self._validate_transition(kinds[edge.parent_node_id], kinds[edge.child_node_id])
            if self._would_cycle(connection, edge):
                raise ValueError("ExperimentEdge would introduce a cycle")
            try:
                connection.execute(
                    """INSERT INTO experiment_edges_v1
                       (experiment_id, parent_node_id, child_node_id, relation)
                       VALUES (?, ?, ?, ?)""",
                    (edge.experiment_id, edge.parent_node_id, edge.child_node_id, edge.relation),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Experiment edge already exists") from exc

    def describe(self, experiment_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT payload_json, payload_sha256 FROM experiment_nodes_v1
                   WHERE experiment_id = ? ORDER BY created_at, node_id""", (experiment_id,)
            ).fetchall()
            edges = connection.execute(
                """SELECT experiment_id, parent_node_id, child_node_id, relation
                   FROM experiment_edges_v1 WHERE experiment_id = ?
                   ORDER BY parent_node_id, child_node_id, relation""", (experiment_id,)
            ).fetchall()
        nodes = []
        for row in rows:
            payload = row["payload_json"]
            actual = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            if actual != row["payload_sha256"]:
                raise RuntimeError("Experiment graph payload integrity check failed")
            nodes.append(ExperimentNode.from_dict(json.loads(payload)).to_dict())
        return {
            "experiment_id": experiment_id,
            "nodes": nodes,
            "edges": [ExperimentEdge(**dict(row)).to_dict() for row in edges],
        }

    @staticmethod
    def _validate_transition(parent: ExperimentNodeKind, child: ExperimentNodeKind) -> None:
        allowed = {
            ExperimentNodeKind.DESIGN_REVISION: {ExperimentNodeKind.BASELINE},
            ExperimentNodeKind.BASELINE: {ExperimentNodeKind.OBSERVATION},
            ExperimentNodeKind.OBSERVATION: {ExperimentNodeKind.DIAGNOSIS, ExperimentNodeKind.PROPOSAL, ExperimentNodeKind.MEMORY},
            ExperimentNodeKind.DIAGNOSIS: {ExperimentNodeKind.PROPOSAL, ExperimentNodeKind.MEMORY},
            ExperimentNodeKind.PROPOSAL: {ExperimentNodeKind.REVIEW},
            ExperimentNodeKind.REVIEW: {ExperimentNodeKind.ATTEMPT, ExperimentNodeKind.DECISION},
            ExperimentNodeKind.ATTEMPT: {ExperimentNodeKind.MEASUREMENT, ExperimentNodeKind.OBSERVATION},
            ExperimentNodeKind.MEASUREMENT: {ExperimentNodeKind.DECISION, ExperimentNodeKind.OBSERVATION},
            ExperimentNodeKind.DECISION: {ExperimentNodeKind.MEMORY, ExperimentNodeKind.PROPOSAL},
            ExperimentNodeKind.MEMORY: {ExperimentNodeKind.PROPOSAL},
        }
        if child not in allowed[parent]:
            raise ValueError(f"Invalid Experiment Graph transition: {parent.value} -> {child.value}")

    @staticmethod
    def _would_cycle(connection: sqlite3.Connection, edge: ExperimentEdge) -> bool:
        todo = [edge.child_node_id]
        visited: set[str] = set()
        while todo:
            node = todo.pop()
            if node == edge.parent_node_id:
                return True
            if node in visited:
                continue
            visited.add(node)
            todo.extend(row[0] for row in connection.execute(
                """SELECT child_node_id FROM experiment_edges_v1
                   WHERE experiment_id = ? AND parent_node_id = ?""",
                (edge.experiment_id, node),
            ))
        return False

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection
