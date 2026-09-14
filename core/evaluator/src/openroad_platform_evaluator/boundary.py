"""The protected evaluation boundary.

The evaluator decides QoR.  Nothing else may.  A model's opinion, a dashboard
number, or an adapter's self-reported metric are all non-authoritative.

The kernel owns the *boundary*; a plugin owns the *domain*.  This module runs
an evaluator capability through the same adapter protocol as any other
capability, and then refuses to accept its answer unless the answer is
traceable:

* an admissible verdict must carry at least one metric
* every metric must point at an artifact that exists in the evaluated workspace
* every artifact the verdict claims must resolve inside that workspace
* a non-admissible verdict must say why

Putting the domain parsers in a plugin is what keeps twenty vendor identifiers
out of the control plane.  v1 put them inside the platform and the result was a
kernel that could not host a second tool without being edited.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openroad_platform_contracts import (
    ArtifactDeclaration,
    ContractError,
    EvaluationRequest,
    EvaluatorPin,
    Metric,
    PROTECTED_EVALUATOR_CAPABILITY,
    PluginManifest,
    RuntimeStatus,
    SCHEMA_VERSION,
    TaskSpec,
    Verdict,
    VerdictStatus,
)

from openroad_platform_runtime import (
    ProcessAdapter,
    RuntimeStoreError,
    sha256,
)

#: The artifact kind an evaluator uses to return its verdict.  Reserved by the
#: platform: an ordinary adapter that declares it is rejected by the contract.
VERDICT_ARTIFACT_KIND = "protected_evaluation"

VERDICT_FILENAME = "protected_evaluation.json"


class EvaluationError(RuntimeError):
    """The evaluator's answer is not acceptable.  Never downgraded to a default."""


@dataclass(frozen=True)
class EvaluationOutcome:
    verdict: Verdict
    pin: EvaluatorPin
    verdict_path: str
    verdict_sha256: str


class PluginBackedEvaluator:
    """Runs a protected evaluator capability and validates what it returns.

    Implements the kernel's ``ProtectedEvaluator`` protocol.  The runtime sees
    only ``evaluate(request) -> Verdict``; this class is where the pinning, the
    subprocess, and the traceability checks live.
    """

    def __init__(
        self,
        *,
        adapter: ProcessAdapter,
        manifest: PluginManifest,
        timeout_seconds: int = 600,
    ):
        manifest.validate()
        if PROTECTED_EVALUATOR_CAPABILITY not in manifest.capabilities:
            raise EvaluationError(
                f"plugin {manifest.plugin_id!r} does not declare "
                f"{PROTECTED_EVALUATOR_CAPABILITY!r}; it cannot be the evaluator"
            )
        self.adapter = adapter
        self.manifest = manifest
        self.pin = EvaluatorPin.of(manifest)
        self.timeout_seconds = timeout_seconds

    # -- the protocol -----------------------------------------------------

    def evaluate(self, request: EvaluationRequest) -> Verdict:
        outcome = self.evaluate_with_evidence(request)
        return outcome.verdict

    def evaluate_with_evidence(self, request: EvaluationRequest) -> EvaluationOutcome:
        """Evaluate, and return the pin and verdict hash alongside the verdict.

        The runtime records the pin with the run.  Two runs are only comparable
        when the same evaluator version produced both, and that is only
        checkable if the version is stored next to the result.
        """
        request.validate()
        workspace = Path(request.workspace).expanduser().resolve()
        if not workspace.is_dir():
            raise EvaluationError(f"evaluation workspace does not exist: {workspace}")

        task = TaskSpec(
            task_id=f"evaluate-{request.attempt_id}",
            project_id=request.task.project_id,
            design_id=request.task.design_id,
            plugin_id=self.manifest.plugin_id,
            inputs={
                "workspace": str(workspace),
                "evaluated_plugin_id": request.manifest.plugin_id,
                "evaluated_task": request.task.to_dict(),
                "declared_artifacts": [
                    {"kind": a.kind, "path": a.path}
                    for a in request.declared_artifacts
                ],
            },
            timeout_seconds=min(self.timeout_seconds,
                                self.manifest.default_timeout_seconds),
            expected_artifacts=(VERDICT_ARTIFACT_KIND,),
        )

        # The evaluator returns a reserved artifact kind, so it is the one
        # capability the platform grants that authority.
        execution = self.adapter.execute(
            self.manifest, task, workspace=workspace,
            allow_reserved_artifacts=True,
        )
        if execution.result.status is not RuntimeStatus.SUCCEEDED:
            raise EvaluationError(
                f"the protected evaluator failed: "
                f"{execution.result.failure or execution.result.status.value}"
            )

        verdict_path = self._locate_verdict(execution.artifacts, workspace)
        verdict = self._load_verdict(verdict_path)
        self._check_traceability(verdict, workspace)
        return EvaluationOutcome(
            verdict=verdict,
            pin=self.pin,
            verdict_path=str(verdict_path),
            verdict_sha256=sha256(verdict_path),
        )

    # -- validation -------------------------------------------------------

    @staticmethod
    def _locate_verdict(
        artifacts: tuple[dict[str, Any], ...], workspace: Path
    ) -> Path:
        matches = [a for a in artifacts if a["kind"] == VERDICT_ARTIFACT_KIND]
        if len(matches) != 1:
            raise EvaluationError(
                f"the evaluator must produce exactly one {VERDICT_ARTIFACT_KIND} "
                f"artifact; found {len(matches)}"
            )
        return (workspace / matches[0]["store_key"]).resolve()

    @staticmethod
    def _load_verdict(path: Path) -> Verdict:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvaluationError(f"verdict is not readable JSON: {exc}") from exc
        try:
            status = VerdictStatus(payload.get("status"))
        except ValueError as exc:
            raise EvaluationError(
                f"verdict status {payload.get('status')!r} is not one of "
                f"{[s.value for s in VerdictStatus]}"
            ) from exc
        metrics = tuple(
            Metric.from_dict({"schema_version": SCHEMA_VERSION, **item})
            for item in payload.get("metrics", ())
        )
        artifacts = tuple(
            ArtifactDeclaration(
                kind=item["kind"], path=item["path"],
                metadata=dict(item.get("metadata") or {}),
            )
            for item in payload.get("artifacts", ())
        )
        try:
            verdict = Verdict(
                status=status, metrics=metrics, artifacts=artifacts,
                reason=payload.get("reason"),
                loss_manifest=dict(payload.get("loss_manifest") or {}),
            )
            verdict.validate()
        except ContractError as exc:
            raise EvaluationError(f"the verdict does not satisfy its contract: {exc}") from exc
        return verdict

    @staticmethod
    def _check_traceability(verdict: Verdict, workspace: Path) -> None:
        """Every claimed artifact must exist, and every metric must cite one.

        This is the check that makes a QoR number worth storing.  An evaluator
        that reports a number citing nothing gets its verdict refused, not
        recorded with a warning.
        """
        known: set[str] = set()
        for artifact in verdict.artifacts:
            path = (workspace / artifact.path).resolve()
            try:
                path.relative_to(workspace)
            except ValueError as exc:
                raise EvaluationError(
                    f"verdict artifact escapes the workspace: {artifact.path!r}"
                ) from exc
            if not path.is_file():
                raise EvaluationError(
                    f"verdict artifact does not exist: {artifact.path!r}"
                )
            known.add(artifact.path)

        for metric in verdict.metrics:
            if metric.source_artifact_id is not None:
                continue
            store_key = metric.context.get("source_artifact_store_key")
            if not store_key:
                raise EvaluationError(
                    f"metric {metric.name!r} cites no source artifact; "
                    f"an unsourced metric is not evidence"
                )
            resolved = (workspace / str(store_key)).resolve()
            try:
                resolved.relative_to(workspace)
            except ValueError as exc:
                raise EvaluationError(
                    f"metric {metric.name!r} cites a path outside the "
                    f"workspace: {store_key!r}"
                ) from exc
            if not resolved.is_file():
                raise EvaluationError(
                    f"metric {metric.name!r} cites an artifact that does "
                    f"not exist: {store_key!r}"
                )


def resolve_evaluator(registry, *, plugin_id: str | None = None) -> PluginManifest:
    """Find the protected evaluator among the admitted capabilities.

    Exactly one is expected.  Two would make a run's QoR depend on which one a
    caller happened to pick, which defeats the point of a protected boundary.
    """
    candidates = [
        plugin for plugin in registry.list()
        if PROTECTED_EVALUATOR_CAPABILITY in plugin.manifest.capabilities
        and plugin.executable
    ]
    if plugin_id is not None:
        candidates = [p for p in candidates if p.manifest.plugin_id == plugin_id]
    if not candidates:
        raise EvaluationError(
            "no admitted plugin declares capability "
            f"{PROTECTED_EVALUATOR_CAPABILITY!r}"
        )
    if len(candidates) > 1:
        names = ", ".join(sorted(p.manifest.plugin_id for p in candidates))
        raise EvaluationError(
            f"several plugins claim to be the protected evaluator: {names}. "
            f"Choose one explicitly."
        )
    return candidates[0].manifest
