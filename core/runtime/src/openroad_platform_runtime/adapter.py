"""The bounded adapter protocol.

A capability runs as a separate process.  The platform hands it a request file
and expects a result file; nothing else crosses the boundary.  This is what
makes "reuse the upstream algorithm" real instead of aspirational: the plugin
keeps its own code, its own environment, and its own dependencies, and the
platform keeps authority over state.

The checks below are the reason a plugin cannot lie about its result:

* the process exit code must agree with the claimed ``exit_code``
* a claimed success with a non-zero exit code is a protocol failure
* every artifact path must resolve inside the attempt workspace
* every artifact kind must be allowed by the manifest
* a required artifact kind must actually be present and non-empty

Anything that fails these becomes a FAILED result with category
``protocol_error``.  It is never repaired by falling back to a local
implementation.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from openroad_platform_contracts import (
    ArtifactDeclaration,
    ContractError,
    PluginManifest,
    PluginResult,
    RuntimeStatus,
    TaskSpec,
    validate_identifier,
)

from .digest import sha256
from .guardian import ProcessGuardian, ProcessOutcome

#: Only these host variables cross into a plugin process.  Everything else --
#: credentials, tokens, unrelated tool configuration -- stays out.  A plugin
#: that needs more declares it in ``manifest.environment``.
SAFE_HOST_ENVIRONMENT = ("HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "TEMP", "TMP")

#: The request/result filenames are part of the protocol, not an implementation
#: detail: a plugin author writes against them.
REQUEST_FILENAME = "adapter_request.json"
RESULT_FILENAME = "adapter_result.json"
LOG_FILENAME = "adapter.log"

#: The wire protocol's own version.  Deliberately *not* called schema_version:
#: the request envelope and the payloads inside it version independently, and a
#: plugin that saw two different keys with the same name could not tell which
#: one it was being asked about.
PROTOCOL_VERSION = 1


@dataclass(frozen=True)
class AdapterExecution:
    """Everything the runtime needs to record one attempt."""

    result: PluginResult
    outcome: ProcessOutcome
    artifacts: tuple[dict[str, Any], ...]
    request_path: str
    result_path: str
    log_path: str


class AdapterProtocolError(Exception):
    """The adapter broke the protocol.  Becomes a FAILED result, loudly."""


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
        environment: Mapping[str, str] | None = None,
        allow_reserved_artifacts: bool = False,
    ) -> AdapterExecution:
        # ``allow_reserved_artifacts`` is the one authority the platform
        # grants to itself when it runs its own evaluator.  It is an explicit
        # argument so that no ordinary capability can acquire it by accident.
        manifest.validate()
        task.validate()
        if task.plugin_id != manifest.plugin_id:
            raise ValueError(
                f"task targets {task.plugin_id!r} but the manifest is "
                f"{manifest.plugin_id!r}"
            )

        root = Path(workspace).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        request_path = root / REQUEST_FILENAME
        result_path = root / RESULT_FILENAME
        log_path = root / LOG_FILENAME

        self._write_request(request_path, manifest, task)

        started_at = _now()
        command = [
            *manifest.adapter_entry,
            "--request", str(request_path),
            "--result", str(result_path),
        ]
        outcome = self.guardian.run(
            command,
            cwd=root,
            env={**self._environment(manifest), **dict(environment or {})},
            log_path=log_path,
            timeout_seconds=min(task.timeout_seconds,
                                manifest.default_timeout_seconds),
            cancel_requested=cancel_requested,
            on_line=on_line,
        )
        ended_at = _now()

        try:
            result = self._load_result(
                result_path, outcome, manifest, started_at=started_at,
                ended_at=ended_at,
            )
            artifacts = self._validate_artifacts(
                root, manifest, task, result,
                require_expected=result.status is RuntimeStatus.SUCCEEDED,
                allow_reserved=allow_reserved_artifacts,
            )
        except AdapterProtocolError as exc:
            result = protocol_failure(started_at, ended_at, outcome.returncode,
                                      str(exc))
            artifacts = ()

        return AdapterExecution(
            result=result,
            outcome=outcome,
            artifacts=artifacts,
            request_path=str(request_path),
            result_path=str(result_path),
            log_path=str(log_path),
        )

    # -- request ----------------------------------------------------------

    @staticmethod
    def _write_request(path: Path, manifest: PluginManifest, task: TaskSpec) -> None:
        payload = {
            "protocol_version": PROTOCOL_VERSION,
            "plugin": {
                "plugin_id": manifest.plugin_id,
                "plugin_version": manifest.plugin_version,
            },
            "task": task.to_dict(),
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        # Atomic hand-off: the adapter must never read a half-written request.
        temporary.replace(path)

    @staticmethod
    def _environment(manifest: PluginManifest) -> dict[str, str]:
        environment = {
            key: os.environ[key]
            for key in SAFE_HOST_ENVIRONMENT
            if key in os.environ
        }
        environment.update(manifest.environment)
        environment["OPENROAD_PLATFORM_PLUGIN_ID"] = manifest.plugin_id
        environment["OPENROAD_PLATFORM_PLUGIN_VERSION"] = manifest.plugin_version
        return environment

    # -- result -----------------------------------------------------------

    @staticmethod
    def _load_result(
        path: Path,
        outcome: ProcessOutcome,
        manifest: PluginManifest,
        *,
        started_at: str,
        ended_at: str,
    ) -> PluginResult:
        # A cancelled or timed-out process has no opinion worth reading: the
        # platform decided the outcome, not the adapter.
        if outcome.cancelled:
            return PluginResult(
                status=RuntimeStatus.CANCELLED, exit_code=outcome.returncode,
                started_at=started_at, ended_at=ended_at,
                failure={"category": "cancelled",
                         "message": "cancellation requested"},
            )
        if outcome.timed_out:
            return PluginResult(
                status=RuntimeStatus.TIMED_OUT, exit_code=outcome.returncode,
                started_at=started_at, ended_at=ended_at,
                failure={"category": "timeout",
                         "message": "adapter deadline exceeded"},
            )
        if not path.is_file():
            raise AdapterProtocolError(
                f"adapter {manifest.plugin_id!r} produced no {RESULT_FILENAME}"
            )
        try:
            result = PluginResult.from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise AdapterProtocolError(f"invalid {RESULT_FILENAME}: {exc}") from exc

        if outcome.returncode != 0 and result.status is RuntimeStatus.SUCCEEDED:
            raise AdapterProtocolError(
                "adapter claimed success with a non-zero process exit code "
                f"({outcome.returncode})"
            )
        if outcome.returncode != result.exit_code:
            raise AdapterProtocolError(
                f"result exit_code {result.exit_code} does not match the "
                f"process exit code {outcome.returncode}"
            )
        return result

    # -- artifacts --------------------------------------------------------

    @staticmethod
    def _validate_artifacts(
        root: Path,
        manifest: PluginManifest,
        task: TaskSpec,
        result: PluginResult,
        *,
        require_expected: bool = True,
        allow_reserved: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        return validate_artifact_declarations(
            root, manifest,
            [
                {"kind": item["kind"], "path": item["path"],
                 "metadata": {k: v for k, v in item.items()
                              if k not in {"kind", "path"}}}
                for item in result.artifacts
            ],
            expected_kinds=task.expected_artifacts,
            require_expected=require_expected,
            allow_reserved=allow_reserved,
        )


def validate_artifact_declarations(
    workspace: str | Path,
    manifest: PluginManifest,
    declarations: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    *,
    expected_kinds: tuple[str, ...] = (),
    require_expected: bool = True,
    allow_reserved: bool = False,
) -> tuple[dict[str, Any], ...]:
    """Check declarations against the workspace and the manifest allowlist.

    Shared by adapter output, protected-evaluator output, and the runtime's
    own bookkeeping.  The first two are held to identical rules, because a
    forged evaluator artifact is as damaging as a forged adapter artifact.

    ``allow_reserved`` is the one deliberate difference: the runtime must be
    able to register kinds that it forbids everyone else from declaring.
    Making it an explicit argument means no caller can gain that authority
    by accident.
    """
    root = Path(workspace).expanduser().resolve()
    allowed_kinds = {
        rule.get("kind") for rule in manifest.artifact_rules if rule.get("kind")
    }
    normalized: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    produced_kinds: list[str] = []

    for item in declarations:
        kind = item.get("kind")
        declared = item.get("path")
        if not isinstance(kind, str) or not isinstance(declared, str):
            raise AdapterProtocolError("artifact declaration needs kind and path")

        # The contract does the traversal, absolute-path and reserved-kind
        # checks so they cannot drift between call sites.  It raises its own
        # error type; translating it here keeps the adapter protocol's error
        # surface single and predictable.
        try:
            declaration = ArtifactDeclaration(
                kind=kind, path=declared,
                metadata=dict(item.get("metadata") or {}),
                allow_reserved=allow_reserved,
            )
            declaration.validate()
        except ContractError as exc:
            raise AdapterProtocolError(str(exc)) from exc

        if allowed_kinds and kind not in allowed_kinds and not allow_reserved:
            # The manifest allowlist describes what the *plugin* may emit.
            # The platform's own bookkeeping is not bound by the plugin's
            # declaration, which is exactly what allow_reserved signals.
            raise AdapterProtocolError(
                f"artifact kind {kind!r} is not allowed by the manifest"
            )
        path = (root / declared).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise AdapterProtocolError(
                f"artifact path escapes the workspace: {declared!r}"
            ) from exc
        if not path.is_file() or path.stat().st_size == 0:
            raise AdapterProtocolError(
                f"artifact is missing or empty: {declared!r}"
            )
        store_key = str(path.relative_to(root))
        if store_key in seen_keys:
            raise AdapterProtocolError(
                f"artifact declared more than once: {store_key!r}"
            )
        seen_keys.add(store_key)
        produced_kinds.append(kind)
        normalized.append({
            "kind": kind,
            "store_key": store_key,
            "size_bytes": path.stat().st_size,
            "sha256": sha256(path),
            "media_type": item.get("media_type"),
            "metadata": dict(item.get("metadata") or {}),
        })

    if require_expected:
        # A failed attempt legitimately produces nothing, so required kinds
        # are only enforced for a result that claims success.
        required = set(expected_kinds)
        required.update(
            rule.get("kind")
            for rule in manifest.artifact_rules
            if rule.get("required")
        )
        missing = sorted(k for k in required if k and k not in produced_kinds)
        if missing:
            raise AdapterProtocolError(
                f"required artifact kinds missing: {', '.join(missing)}"
            )
    return tuple(normalized)


def protocol_failure(
    started_at: str, ended_at: str, exit_code: int, message: str
) -> PluginResult:
    return PluginResult(
        status=RuntimeStatus.FAILED,
        exit_code=exit_code if exit_code != 0 else 1,
        started_at=started_at,
        ended_at=ended_at,
        failure={"category": "protocol_error", "message": message},
    )


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
