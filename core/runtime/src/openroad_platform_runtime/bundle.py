"""Local, evidence-complete run export."""

from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from openroad_platform_contracts import Artifact

from .digest import sha256
from .store import STORAGE_OBJECT, RuntimeStore, RuntimeStoreError, _new_id, _now


def _register_bytes(
    store: RuntimeStore,
    attempt_id: str,
    *,
    store_key: str,
    kind: str,
    content: bytes,
    metadata: dict[str, Any],
) -> Artifact:
    artifact = Artifact(
        artifact_id=_new_id("art"),
        kind=kind,
        store_key=store_key,
        sha256=sha256(content),
        size_bytes=len(content),
        metadata=metadata,
    )
    artifact.validate()
    with store._lock:
        existing = store._connection.execute(
            "SELECT artifact_id FROM runtime_artifacts WHERE attempt_id = ? AND store_key = ?",
            (attempt_id, store_key),
        ).fetchone()
        if existing is not None:
            return store.get_artifact(str(existing["artifact_id"]))
        store._store_bytes(content, artifact.sha256, artifact.size_bytes)
        store._connection.execute(
            "INSERT INTO runtime_artifacts (artifact_id, attempt_id, kind, store_key, "
            "size_bytes, sha256, metadata_json, created_at, storage) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                artifact.artifact_id,
                attempt_id,
                artifact.kind,
                artifact.store_key,
                artifact.size_bytes,
                artifact.sha256,
                json.dumps(metadata, sort_keys=True),
                _now(),
                STORAGE_OBJECT,
            ),
        )
    return artifact


def _register_file(
    store: RuntimeStore,
    attempt_id: str,
    *,
    store_key: str,
    kind: str,
    path: Path,
    metadata: dict[str, Any],
) -> Artifact:
    artifact = Artifact(artifact_id=_new_id("art"), kind=kind, store_key=store_key,
                        sha256=sha256(path), size_bytes=path.stat().st_size,
                        metadata=metadata)
    artifact.validate()
    with store._lock:
        existing = store._connection.execute(
            "SELECT artifact_id FROM runtime_artifacts WHERE attempt_id = ? AND store_key = ?",
            (attempt_id, store_key),
        ).fetchone()
        if existing is not None:
            return store.get_artifact(str(existing["artifact_id"]))
        store._store_object(path, artifact.sha256, artifact.size_bytes)
        store._connection.execute(
            "INSERT INTO runtime_artifacts (artifact_id, attempt_id, kind, store_key, "
            "size_bytes, sha256, metadata_json, created_at, storage) VALUES (?,?,?,?,?,?,?,?,?)",
            (artifact.artifact_id, attempt_id, artifact.kind, artifact.store_key,
             artifact.size_bytes, artifact.sha256, json.dumps(metadata, sort_keys=True),
             _now(), STORAGE_OBJECT),
        )
    return artifact


def export_run_bundle(store: RuntimeStore, run_id: str) -> dict[str, Any]:
    """Create one content-addressed ZIP containing a run's evidence tree."""
    view = store.describe_run(run_id)
    attempts = [(stage, attempt) for stage in view["stages"] for attempt in stage["attempts"]]
    if not attempts:
        raise RuntimeStoreError("a run without an attempt has nothing to export")
    _, latest = attempts[-1]
    for artifact in latest["artifacts"]:
        if artifact["kind"] == "runtime_bundle":
            return artifact

    entries: list[dict[str, Any]] = []
    with tempfile.NamedTemporaryFile(prefix="runtime-bundle-", suffix=".zip", delete=False) as temporary:
        payload_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(payload_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "run.json",
                json.dumps(view, ensure_ascii=False, sort_keys=True, indent=2),
            )
            archive.writestr(
                "events.json",
                json.dumps(
                    [
                        {
                            "event_id": event.event_id,
                            "run_id": event.run_id,
                            "event_type": event.event_type,
                            "occurred_at": event.occurred_at,
                            "producer": event.producer,
                            "payload": event.payload,
                            "stage_run_id": event.stage_run_id,
                            "attempt_id": event.attempt_id,
                        }
                        for event in store.list_events(run_id)
                    ],
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                ),
            )
            for current_stage in view["stages"]:
                for attempt in current_stage["attempts"]:
                    workspace = Path(attempt["workspace"]).resolve()
                    for filename in (
                        "adapter_request.json",
                        "adapter_result.json",
                        "adapter.log",
                        "runtime_input_manifest.json",
                        "runtime_protocol_receipt.json",
                    ):
                        source = store.evidence_path(attempt["attempt_id"], filename)
                        if not source.is_file():
                            source = workspace / filename
                        if not source.is_file():
                            continue
                        name = f"runtime/{current_stage['stage_key']}/attempt-{attempt['attempt_number']}/{filename}"
                        archive.write(source, name)
                        entries.append(
                            {
                                "path": name,
                                "kind": "runtime_file",
                                "sha256": sha256(source),
                                "size_bytes": source.stat().st_size,
                            }
                        )
                    for staged in attempt["inputs"]:
                        if not staged.get("present"):
                            continue
                        source = None
                        if staged.get("source_input_id"):
                            source = store.object_path(store.get_uploaded_input(staged["source_input_id"]).sha256)
                        if source is None:
                            source = (workspace / staged["destination"]).resolve()
                        if not staged.get("source_input_id"):
                            try:
                                source.relative_to(workspace)
                            except ValueError as exc:
                                raise RuntimeStoreError(
                                    f"staged input escapes its workspace: {staged['destination']!r}"
                                ) from exc
                        if not source.is_file() or (staged.get("sha256") and sha256(source) != staged["sha256"]):
                            raise RuntimeStoreError(f"staged input {staged['destination']!r} failed hash verification")
                        name = (
                            f"inputs/{current_stage['stage_key']}/"
                            f"attempt-{attempt['attempt_number']}/{staged['destination']}"
                        )
                        archive.write(source, name)
                        entries.append({"path": name, "input": staged})
                    for artifact in attempt["artifacts"]:
                        if artifact["kind"] == "runtime_bundle":
                            continue
                        path = store.artifact_path(artifact["artifact_id"])
                        if not path.is_file() or sha256(path) != artifact["sha256"]:
                            raise RuntimeStoreError(f"artifact {artifact['artifact_id']!r} failed hash verification")
                        name = (
                            f"artifacts/{current_stage['stage_key']}/"
                            f"attempt-{attempt['attempt_number']}/{artifact['store_key']}"
                        )
                        archive.write(path, name)
                        entries.append({"path": name, **artifact})
            archive.writestr(
                "artifacts/index.json",
                json.dumps(
                    entries,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                ),
            )
        created = _register_file(
            store,
            latest["attempt_id"],
            store_key="runtime_bundle.zip",
            kind="runtime_bundle",
            path=payload_path,
            metadata={"producer": "runtime", "format": "zip", "complete": True},
        )
    finally:
        payload_path.unlink(missing_ok=True)
    return {
        "artifact_id": created.artifact_id,
        "kind": created.kind,
        "store_key": created.store_key,
        "sha256": created.sha256,
        "size_bytes": created.size_bytes,
        "metadata": created.metadata,
        "storage": "object",
        "run_id": run_id,
    }
