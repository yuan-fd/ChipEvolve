"""Versioned task/result-file subprocess adapter protocol."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from openroad_platform_contracts import (
    PluginManifest,
    PluginResult,
    RuntimeStatus,
    TaskSpec,
)

from .process_guardian import ProcessGuardian, ProcessOutcome


SAFE_HOST_ENVIRONMENT = ("HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "TEMP", "TMP")


@dataclass(frozen=True)
class AdapterExecution:
    result: PluginResult
    outcome: ProcessOutcome
    artifacts: tuple[dict, ...]
    request_path: str
    result_path: str
    log_path: str


class ProcessAdapter:
    def __init__(self, guardian: ProcessGuardian | None = None):
        self.guardian = guardian or ProcessGuardian()

    def execute(
        self,
        manifest: PluginManifest,
        task: TaskSpec,
        *,
        workspace: str | Path,
        cancel_requested: Callable[[], bool] | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> AdapterExecution:
        manifest.validate()
        task.validate()
        if task.plugin_id != manifest.plugin_id:
            raise ValueError(
                f"Task targets {task.plugin_id}, not manifest plugin {manifest.plugin_id}"
            )
        root = Path(workspace).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        request_path = root / "adapter_request.json"
        result_path = root / "adapter_result.json"
        log_path = root / "adapter.log"
        self._write_json(request_path, {
            "schema_version": 1,
            "plugin": {
                "plugin_id": manifest.plugin_id,
                "plugin_version": manifest.plugin_version,
            },
            "task": task.to_dict(),
        })
        started = _now()
        command = [
            *manifest.adapter_entry,
            "--request", str(request_path),
            "--result", str(result_path),
        ]
        outcome = self.guardian.run(
            command,
            cwd=root,
            env=self._environment(manifest),
            log_path=log_path,
            timeout_seconds=min(task.timeout_seconds, manifest.default_timeout_seconds),
            cancel_requested=cancel_requested,
            on_line=on_line,
        )
        result = self._load_result(
            result_path, outcome, started_at=started, ended_at=_now()
        )
        artifacts: tuple[dict, ...] = ()
        if result.status is RuntimeStatus.SUCCEEDED:
            try:
                artifacts = self._validate_artifacts(root, manifest, task, result)
            except (OSError, ValueError) as exc:
                result = _protocol_failure(
                    started, _now(), outcome.returncode,
                    f"Artifact protocol error: {exc}",
                )
        return AdapterExecution(
            result=result,
            outcome=outcome,
            artifacts=artifacts,
            request_path=str(request_path),
            result_path=str(result_path),
            log_path=str(log_path),
        )

    @staticmethod
    def _environment(manifest: PluginManifest) -> dict[str, str]:
        env = {key: os.environ[key] for key in SAFE_HOST_ENVIRONMENT if key in os.environ}
        env.update(manifest.environment)
        env["OPENROAD_PLATFORM_PLUGIN_ID"] = manifest.plugin_id
        env["OPENROAD_PLATFORM_PLUGIN_VERSION"] = manifest.plugin_version
        return env

    @staticmethod
    def _load_result(
        path: Path,
        outcome: ProcessOutcome,
        *,
        started_at: str,
        ended_at: str,
    ) -> PluginResult:
        if outcome.cancelled:
            return PluginResult(
                status=RuntimeStatus.CANCELLED, exit_code=outcome.returncode,
                started_at=started_at, ended_at=ended_at,
                failure={"category": "cancelled", "message": "Cancellation requested"},
            )
        if outcome.timed_out:
            return PluginResult(
                status=RuntimeStatus.TIMED_OUT, exit_code=outcome.returncode,
                started_at=started_at, ended_at=ended_at,
                failure={"category": "timeout", "message": "Adapter deadline exceeded"},
            )
        if not path.is_file():
            return _protocol_failure(
                started_at, ended_at, outcome.returncode,
                "Adapter did not produce adapter_result.json",
            )
        try:
            result = PluginResult.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return _protocol_failure(
                started_at, ended_at, outcome.returncode,
                f"Invalid adapter_result.json: {exc}",
            )
        if outcome.returncode != 0 and result.status is RuntimeStatus.SUCCEEDED:
            return _protocol_failure(
                started_at, ended_at, outcome.returncode,
                "Adapter claimed success with a non-zero process exit code",
            )
        if outcome.returncode != result.exit_code:
            return _protocol_failure(
                started_at, ended_at, outcome.returncode,
                "Adapter result exit_code does not match the process exit code",
            )
        return result

    @staticmethod
    def _validate_artifacts(
        root: Path,
        manifest: PluginManifest,
        task: TaskSpec,
        result: PluginResult,
    ) -> tuple[dict, ...]:
        normalized = []
        kinds = []
        allowed_kinds = {
            rule.get("kind") for rule in manifest.artifact_rules if rule.get("kind")
        }
        for item in result.artifacts:
            relative = Path(item["path"])
            if relative.is_absolute():
                raise ValueError(f"Artifact path is absolute: {relative}")
            path = (root / relative).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"Artifact path is outside the workspace: {relative}") from exc
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError(f"Artifact is missing or empty: {relative}")
            kind = item["kind"]
            if allowed_kinds and kind not in allowed_kinds:
                raise ValueError(f"Artifact kind is not allowed by manifest: {kind}")
            kinds.append(kind)
            normalized.append({
                "kind": kind,
                "store_key": str(path.relative_to(root)),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
                "metadata": {
                    key: value for key, value in item.items() if key not in {"kind", "path"}
                },
            })
        required = set(task.expected_artifacts)
        required.update(
            rule.get("kind") for rule in manifest.artifact_rules if rule.get("required")
        )
        missing = sorted(kind for kind in required if kind and kind not in kinds)
        if missing:
            raise ValueError(f"Required artifact kinds missing: {', '.join(missing)}")
        return tuple(normalized)

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)


def _protocol_failure(
    started_at: str,
    ended_at: str,
    exit_code: int,
    message: str,
) -> PluginResult:
    return PluginResult(
        status=RuntimeStatus.FAILED,
        exit_code=exit_code if exit_code != 0 else 1,
        started_at=started_at,
        ended_at=ended_at,
        failure={"category": "protocol_error", "message": message},
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
