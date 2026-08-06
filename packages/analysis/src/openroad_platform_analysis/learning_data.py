"""Read-only Runtime evidence export and immutable learning-dataset storage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from openroad_platform_contracts import (
    EvidencePointer,
    LearningContext,
    LearningObservation,
)


def _canonical_digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


ORFS_QOR_STORE_KEY = "orfs/implementation/analysis/report.json"
ORFS_QOR_UNITS = {
    "area_um2": "um2",
    "setup_wns_ns": "ns",
    "wirelength_um": "um",
    "power_W": "W",
    "drc_errors": "count",
    "runtime_seconds": "s",
}


class RuntimeEvidenceExporter:
    """Convert one immutable Runtime attempt into an observed-only sample."""

    def __init__(self, runtime_store):
        self.runtime_store = runtime_store

    def export_run(self, run_id: str, context: LearningContext) -> LearningObservation:
        context.validate()
        run = self.runtime_store.get_run(run_id)
        if run.task_spec.design_id != context.design_id:
            raise ValueError("Runtime design does not match learning context")
        task_rtl_sha = run.task_spec.inputs.get("rtl_sha256")
        if task_rtl_sha is None and isinstance(run.task_spec.inputs.get("rtl"), dict):
            task_rtl_sha = run.task_spec.inputs["rtl"].get("sha256")
        if task_rtl_sha is not None and task_rtl_sha != context.design_fingerprint:
            raise ValueError("Runtime RTL fingerprint does not match learning context")
        task_platform = run.task_spec.parameters.get("platform")
        if task_platform is not None and task_platform != context.platform:
            raise ValueError("Runtime platform does not match learning context")
        stages = self.runtime_store.list_stages(run_id)
        attempts = [attempt for stage in stages
                    for attempt in self.runtime_store.list_attempts(stage.stage_run_id)]
        if not attempts:
            raise ValueError("Runtime run has no execution attempt")
        successful = [attempt for attempt in attempts if attempt.status.value == "succeeded"]
        attempt = successful[-1] if successful else attempts[-1]
        metric_records = self.runtime_store.metrics(attempt.attempt_id)
        metrics = {}
        units = {}
        for metric in metric_records:
            value = metric.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            metrics[metric["name"]] = float(value)
            if metric.get("unit"):
                units[metric["name"]] = str(metric["unit"])
        artifacts = self.runtime_store.artifacts(attempt.attempt_id)
        qor_artifact = next((item for item in artifacts
                             if item.get("kind") == "report"
                             and item.get("store_key") == ORFS_QOR_STORE_KEY), None)
        if qor_artifact is not None:
            report_metrics, report_units = self._verified_orfs_qor(attempt, qor_artifact)
            for name, value in report_metrics.items():
                if name in metrics and metrics[name] != value:
                    raise ValueError(f"Runtime metric conflicts with verified QoR report: {name}")
                metrics[name] = value
            units.update(report_units)
        parameters = {
            name: float(value) for name, value in run.task_spec.parameters.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        description = self.runtime_store.describe_run(run_id)
        evidence = [EvidencePointer(ref=f"run:{run_id}",
                                    sha256=_canonical_digest(description))]
        for artifact in artifacts:
            digest = artifact.get("sha256")
            if isinstance(digest, str) and len(digest) == 64:
                evidence.append(EvidencePointer(
                    ref=f"artifact:{artifact['artifact_id']}", sha256=digest,
                ))
        started = datetime.fromisoformat(attempt.started_at)
        ended = datetime.fromisoformat(attempt.ended_at) if attempt.ended_at else started
        failure = attempt.failure or {}
        seed = _canonical_digest({
            "run_id": run_id, "attempt_id": attempt.attempt_id,
            "context_fingerprint": context.fingerprint,
        })[:24]
        observation = LearningObservation(
            observation_id=f"observation-{seed}", context=context,
            parameters=parameters, metrics=metrics, metric_units=units,
            status=attempt.status.value,
            cost_seconds=max(0.0, (ended - started).total_seconds()),
            run_id=run_id, attempt_id=attempt.attempt_id,
            evidence=tuple(evidence),
            failure_category=str(failure.get("category")) if failure.get("category") else None,
        )
        observation.validate()
        return observation

    @staticmethod
    def _verified_orfs_qor(attempt, artifact: dict[str, Any]) \
            -> tuple[dict[str, float], dict[str, str]]:
        """Read QoR only after matching the immutable Runtime artifact record."""
        workspace = Path(attempt.workspace).expanduser().resolve()
        store_key = artifact.get("store_key")
        if store_key != ORFS_QOR_STORE_KEY:
            raise ValueError("QoR artifact store key is not allowlisted")
        path = (workspace / store_key).resolve()
        try:
            path.relative_to(workspace)
        except ValueError as exc:
            raise ValueError("QoR artifact escapes the Runtime workspace") from exc
        if not path.is_file():
            raise ValueError("Registered QoR artifact is missing")
        size = path.stat().st_size
        if size != artifact.get("size_bytes") or size > 16 * 1024 * 1024:
            raise ValueError("Registered QoR artifact size mismatch")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != artifact.get("sha256"):
            raise ValueError("Registered QoR artifact SHA-256 mismatch")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Registered QoR artifact is not valid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("kpi"), dict):
            raise ValueError("Registered QoR artifact has no KPI object")
        result: dict[str, float] = {}
        for name in ("area_um2", "setup_wns_ns", "wirelength_um", "power_W",
                     "drc_errors"):
            value = payload["kpi"].get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            result[name] = float(value)
        runtime_seconds = payload.get("runtime_seconds")
        if isinstance(runtime_seconds, (int, float)) and not isinstance(runtime_seconds, bool):
            result["runtime_seconds"] = float(runtime_seconds)
        if not result:
            raise ValueError("Registered QoR artifact contains no numeric allowlisted KPI")
        return result, {name: ORFS_QOR_UNITS[name] for name in result}


class LearningDatasetStore:
    """Append-only observation store; predictions have no insertion path here."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS learning_observations_v1 (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    observation_id TEXT NOT NULL UNIQUE,
                    context_fingerprint TEXT NOT NULL,
                    design_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    source TEXT NOT NULL CHECK(source = 'observed'),
                    payload_json TEXT NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, attempt_id, context_fingerprint)
                );
                CREATE INDEX IF NOT EXISTS idx_learning_observation_context
                ON learning_observations_v1(context_fingerprint, design_id, sequence);
            """)

    def add(self, observation: LearningObservation) -> str:
        observation.validate()
        payload = observation.to_dict()
        with self._connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO learning_observations_v1
                       (observation_id, context_fingerprint, design_id, run_id,
                        attempt_id, source, payload_json, fingerprint, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                    (observation.observation_id, observation.context.fingerprint,
                     observation.context.design_id, observation.run_id,
                     observation.attempt_id, observation.source,
                     json.dumps(payload, ensure_ascii=False), observation.fingerprint),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    "SELECT fingerprint FROM learning_observations_v1 WHERE observation_id = ?",
                    (observation.observation_id,),
                ).fetchone()
                if row is None or row["fingerprint"] != observation.fingerprint:
                    raise ValueError("Observation identity conflicts with existing evidence")
        return observation.observation_id

    def list(self, *, context_fingerprint: str | None = None,
             design_id: str | None = None) -> list[LearningObservation]:
        clauses = []
        values = []
        if context_fingerprint is not None:
            clauses.append("context_fingerprint = ?")
            values.append(context_fingerprint)
        if design_id is not None:
            clauses.append("design_id = ?")
            values.append(design_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM learning_observations_v1" + where
                + " ORDER BY sequence", values,
            ).fetchall()
        return [LearningObservation.from_dict(json.loads(row["payload_json"])) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection
