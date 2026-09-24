"""HTTP translation layer for the kernel."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openroad_platform_contracts import ContractError, TaskSpec
from openroad_platform_identity import AuthSession, IdentityStore
from openroad_platform_provenance import EvidenceIndex
from openroad_platform_registry import PluginRegistry, RegistryError
from openroad_platform_runtime import (
    ArtifactInventory,
    InputStagingError,
    LogQuery,
    ResourceLimitsUnsupported,
    ResourceQuery,
    RuntimeStore,
    RuntimeStoreError,
    WorkflowRuntime,
    queue_health,
    worker_health,
)
from openroad_platform_runtime.bundle import export_run_bundle

from .router import MAX_RESPONSE_BYTES, HttpError, Request, Response, Router

#: Routes reachable without a session.  Anything else requires one.
PUBLIC_PATHS = frozenset(
    {
        ("GET", "/kernel/health"),
        ("POST", "/kernel/auth/register"),
        ("POST", "/kernel/auth/login"),
    }
)
MAX_INPUT_UPLOAD_BYTES = 64 * 1024**3
INPUT_UPLOAD_TTL_SECONDS = 24 * 60 * 60


@dataclass
class KernelApi:
    """The kernel objects, exposed over HTTP."""

    store: RuntimeStore
    runtime: WorkflowRuntime
    registry: PluginRegistry
    identity: IdentityStore
    index: EvidenceIndex
    #: When true, requests without a token act as the shared local user.
    allow_anonymous: bool = False
    local_user_id: str | None = None
    evaluator_error: str | None = None

    # -- registration -----------------------------------------------------

    def register(self, router: Router) -> None:
        router.get("/kernel/health", self._guarded(self.health))
        router.post("/kernel/auth/register", self._guarded(self.auth_register))
        router.post("/kernel/auth/login", self._guarded(self.auth_login))
        router.get("/kernel/auth/session", self._guarded(self.auth_session))
        router.post("/kernel/auth/logout", self._guarded(self.auth_logout))

        router.get("/kernel/plugins", self._guarded(self.plugins))

        router.post("/kernel/inputs", self._guarded(self.ingest_input))
        router.post("/kernel/inputs/chunk", self._guarded(self.ingest_input_chunk))
        router.post("/kernel/runs", self._guarded(self.submit_run))
        router.get("/kernel/runs", self._guarded(self.list_runs))
        router.get("/kernel/runs/{run_id}", self._guarded(self.get_run))
        router.get("/kernel/designs/{design_id}/tree", self._guarded(self.design_tree))
        router.post("/kernel/runs/{run_id}/cancel", self._guarded(self.cancel_run))
        router.post("/kernel/runs/{run_id}/retry", self._guarded(self.retry_run))
        router.post("/kernel/runs/{run_id}/bundle", self._guarded(self.bundle))
        router.get("/kernel/runs/{run_id}/metrics", self._guarded(self.metrics))
        router.get("/kernel/runs/{run_id}/artifacts", self._guarded(self.artifacts))
        router.get("/kernel/runs/{run_id}/timeline", self._guarded(self.timeline))
        router.get("/kernel/runs/{run_id}/resources", self._guarded(self.resources))
        router.get("/kernel/runs/{run_id}/logs", self._guarded(self.logs))
        router.get(
            "/kernel/runs/{run_id}/artifacts/{artifact_id}/excerpt",
            self._guarded(self.artifact_excerpt),
        )
        router.get(
            "/kernel/runs/{run_id}/artifacts/{artifact_id}/download",
            self._guarded(self.artifact_download),
        )
        router.get(
            "/kernel/runs/{run_id}/artifacts/{artifact_id}/chunk",
            self._guarded(self.artifact_chunk),
        )
        router.get("/kernel/graph", self._guarded(self.graph))

    def _guarded(self, handler: Callable[[Request, AuthSession | None], Response]) -> Callable[[Request], Response]:
        def wrapper(request: Request) -> Response:
            session = self._authenticate(request)
            # "This route needs no session" and "this caller has no session"
            # are different facts.  Conflating them makes every public route
            # return 401, which is what the end-to-end test caught.
            if session is None and not self._is_public(request):
                raise HttpError(401, "authentication required")
            return handler(request, session)

        return wrapper

    @staticmethod
    def _is_public(request: Request) -> bool:
        return (request.method, request.path) in PUBLIC_PATHS

    def _authenticate(self, request: Request) -> AuthSession | None:
        header = request.header("Authorization", "") or ""
        if header.lower().startswith("bearer "):
            session = self.identity.resolve(header[7:].strip())
            if session is not None:
                return session
        if self.allow_anonymous and self.local_user_id:
            # The internal no-auth mode still has a real identity, because
            # resource ownership has a foreign key onto the user table.
            return AuthSession(self.local_user_id, "local-user", True, "session-local")
        return None

    # -- health and catalogue ---------------------------------------------

    def health(self, request: Request, session: AuthSession | None) -> Response:
        return Response.json(
            {
                "service": "kernel",
                "status": "ok",
                "plugins": len(self.registry.list()),
                "admitted": sum(1 for p in self.registry.list() if p.executable),
                "evaluator": {
                    "status": "ready" if self.evaluator_error is None else "unavailable",
                    **({"error": self.evaluator_error} if self.evaluator_error else {}),
                },
                "workers": worker_health(self.store),
                "queue": queue_health(self.store),
            }
        )

    def plugins(self, request: Request, session: AuthSession | None) -> Response:
        return Response.json({"plugins": self.registry.catalogue()})

    def ingest_input(self, request: Request, session: AuthSession | None) -> Response:
        if not isinstance(request.body, bytes):
            raise HttpError(400, "input upload requires application/octet-stream")
        uploaded = self.store.ingest_input(request.body)
        owner = session.user_id if session else self.local_user_id
        if owner:
            self.identity.bind_resource("input", uploaded.input_id, owner)
        return Response.json({"input": uploaded.to_dict()}, status=201)

    def ingest_input_chunk(self, request: Request, session: AuthSession | None) -> Response:
        if not isinstance(request.body, bytes):
            raise HttpError(400, "input upload requires application/octet-stream")
        upload_id = request.q("upload_id") or ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", upload_id):
            raise HttpError(400, "upload_id must be a random opaque token")
        offset = request.q_int("offset", 0) or 0
        if offset < 0:
            raise HttpError(400, "offset must not be negative")
        directory = self.store.objects_root / "uploads"
        path = directory / upload_id
        directory.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - INPUT_UPLOAD_TTL_SECONDS
        for stale in directory.iterdir():
            if stale.is_file() and stale.stat().st_mtime < cutoff:
                stale.unlink(missing_ok=True)
        current = path.stat().st_size if path.is_file() else 0
        if current != offset:
            raise HttpError(409, f"upload offset is {current}, not {offset}")
        if current + len(request.body) > MAX_INPUT_UPLOAD_BYTES:
            raise HttpError(413, "input upload exceeds the 64 GiB limit")
        with path.open("ab") as output:
            output.write(request.body)
        if not request.q_bool("final"):
            return Response.json({"upload_id": upload_id, "offset": current + len(request.body)})
        try:
            uploaded = self.store.ingest_input_file(path)
        finally:
            path.unlink(missing_ok=True)
        owner = session.user_id if session else self.local_user_id
        if owner:
            self.identity.bind_resource("input", uploaded.input_id, owner)
        return Response.json({"input": uploaded.to_dict()}, status=201)

    # -- identity ---------------------------------------------------------

    def auth_register(self, request: Request, session: AuthSession | None) -> Response:
        body = _object(request)
        try:
            auth, token = self.identity.register(str(body.get("username") or ""), str(body.get("password") or ""))
        except ValueError as exc:
            raise HttpError(400, str(exc)) from exc
        return Response.json({"token": token, "session": auth.public()})

    def auth_login(self, request: Request, session: AuthSession | None) -> Response:
        body = _object(request)
        try:
            auth, token = self.identity.login(str(body.get("username") or ""), str(body.get("password") or ""))
        except ValueError as exc:
            # A wrong password is 401, not 400: the request was well formed.
            raise HttpError(401, str(exc)) from exc
        return Response.json({"token": token, "session": auth.public()})

    def auth_session(self, request: Request, session: AuthSession | None) -> Response:
        return Response.json({"session": session.public() if session else None})

    def auth_logout(self, request: Request, session: AuthSession | None) -> Response:
        header = request.header("Authorization", "") or ""
        if header.lower().startswith("bearer "):
            self.identity.logout(header[7:].strip())
        return Response.json({"ok": True})

    # -- runs -------------------------------------------------------------

    def submit_run(self, request: Request, session: AuthSession | None) -> Response:
        body = _object(request)
        task_payload = body.get("task")
        if not isinstance(task_payload, dict):
            raise HttpError(400, "body must contain a task object")
        try:
            task = TaskSpec.from_dict(task_payload)
        except ContractError as exc:
            raise HttpError(400, str(exc)) from exc

        for declaration in task.staged_inputs:
            if (declaration.source is not None and session is not None
                    and Path(declaration.source).is_file()
                    and session.username != "local-user"
                    and not self.runtime.config.allowed_input_roots):
                raise HttpError(
                    400,
                    "shared sessions must upload inputs or configure --input-root",
                )
            if declaration.input_id is not None:
                self._require_ownership("input", declaration.input_id, session)
            if declaration.artifact_id is not None:
                try:
                    artifact_run = self.store.artifact_run_id(declaration.artifact_id)
                except RuntimeStoreError as exc:
                    raise HttpError(400, str(exc)) from exc
                self._require_ownership("run", artifact_run, session)

        try:
            idempotency_key = request.q("idempotency_key")
            run = (
                self.runtime.submit_idempotent(task, idempotency_key=idempotency_key)
                if request.q("idempotent") or idempotency_key
                else self.runtime.submit(task)
            )
        except (
            RegistryError,
            InputStagingError,
            ResourceLimitsUnsupported,
            ValueError,
        ) as exc:
            # A task naming a capability that is not available, or inputs that
            # are not where it said, is the caller's mistake, not the server's.
            # Unhandled, this reached the client as a 500, which sends an
            # operator to investigate the platform for a problem that is in
            # their own request.
            raise HttpError(400, str(exc)) from exc
        except RuntimeStoreError as exc:
            raise HttpError(409 if idempotency_key else 500, str(exc)) from exc
        owner = session.user_id if session else self.local_user_id
        if owner:
            self.identity.bind_resource("run", run.run_id, owner)
            # Host files are frozen into platform-owned inputs during submit.
            # Bind those generated records to the same caller before returning
            # the run, so a later run cannot reuse them by guessing an id.
            for declaration in run.task_spec.staged_inputs:
                if declaration.input_id is not None:
                    self.identity.bind_resource("input", declaration.input_id, owner)
        return Response.json({"run": self._run_detail(run.run_id)}, status=201)

    def list_runs(self, request: Request, session: AuthSession | None) -> Response:
        limit = request.q_int("limit")
        offset = request.q_int("offset", 0) or 0
        try:
            owner_ids = None
            if session is not None and not session.developer:
                owner_ids = set(self.identity.resources_owned("run", session.user_id))
            summaries = self.index.runs(
                project_id=request.q("project_id"),
                design_id=request.q("design_id"),
                plugin_id=request.q("plugin_id"),
                status=request.q("status"),
                owner_run_ids=owner_ids,
                offset=offset,
                **({"limit": limit} if limit is not None else {}),
            )
        except ValueError as exc:
            raise HttpError(400, str(exc)) from exc

        return Response.json({"runs": [s.to_dict() for s in summaries]})

    def get_run(self, request: Request, session: AuthSession | None) -> Response:
        run_id = request.params["run_id"]
        self._require_ownership("run", run_id, session)
        try:
            return Response.json({"run": self._run_detail(run_id)})
        except RuntimeStoreError as exc:
            raise HttpError(404, str(exc)) from exc

    def design_tree(self, request: Request, session: AuthSession | None) -> Response:
        design_id = request.params["design_id"]
        offset = request.q_int("offset", 0) or 0
        limit = request.q_int("limit", 500) or 500
        if not 1 <= limit <= 500:
            raise HttpError(400, "limit must be between 1 and 500")
        owner_ids = None
        if session is not None and not session.developer:
            owner_ids = set(self.identity.resources_owned("run", session.user_id))
        summaries = self.index.runs(
            design_id=design_id, owner_run_ids=owner_ids,
            offset=offset, limit=min(limit + 1, 500),
        )
        has_more = len(summaries) > limit or (limit == 500 and len(summaries) == 500)
        summaries = summaries[:limit]
        total = self.index.count_runs(design_id=design_id, owner_run_ids=owner_ids)
        revisions: dict[str, dict[str, Any]] = {}
        for summary in summaries:
            revision = summary.design_revision_id or "unversioned"
            node = self._run_detail(summary.run_id)
            revisions.setdefault(
                revision,
                {
                    "design_revision_id": summary.design_revision_id,
                    "runs": [],
                },
            )["runs"].append(node)
        return Response.json(
            {
                "design_id": design_id,
                "revisions": list(revisions.values()),
                "run_count": total,
                "offset": offset,
                "limit": limit,
                "has_more": has_more,
                "next_offset": offset + limit if has_more else None,
            }
        )

    def _run_detail(self, run_id: str) -> dict[str, Any]:
        """The run, plus the one thing a queued run cannot currently say.

        ``queued`` looks the same whether the machine is full, whether no worker
        is running, or whether something is stuck -- and those need three
        different responses from whoever is looking.  The platform knows which,
        so it says, rather than making an operator open the database to find out.
        """
        detail = self.index.run_detail(run_id)
        detail["waiting_for"] = ResourceQuery(self.store, self.runtime.config).waiting_for(run_id)
        return detail

    def cancel_run(self, request: Request, session: AuthSession | None) -> Response:
        run_id = request.params["run_id"]
        self._require_ownership("run", run_id, session)
        try:
            self.store.request_cancel(run_id)
        except RuntimeStoreError as exc:
            raise HttpError(404, str(exc)) from exc
        return Response.json({"run": self._run_detail(run_id)})

    def retry_run(self, request: Request, session: AuthSession | None) -> Response:
        run_id = request.params["run_id"]
        self._require_ownership("run", run_id, session)
        body = _object(request)
        reason = str(body.get("reason") or "")
        requester = session.user_id if session else self.local_user_id
        try:
            detail = self.store.request_retry(run_id, requester=requester or "anonymous", reason=reason)
        except (RuntimeStoreError, ValueError) as exc:
            raise HttpError(400, str(exc)) from exc
        return Response.json({"run": detail}, status=202)

    def bundle(self, request: Request, session: AuthSession | None) -> Response:
        run_id = request.params["run_id"]
        self._require_ownership("run", run_id, session)
        try:
            return Response.json({"bundle": export_run_bundle(self.store, run_id)}, status=201)
        except RuntimeStoreError as exc:
            raise HttpError(409, str(exc)) from exc

    def metrics(self, request: Request, session: AuthSession | None) -> Response:
        run_id = request.params["run_id"]
        self._require_ownership("run", run_id, session)
        entries = self.index.metrics(run_id, complete_only=request.q_bool("complete_only"))
        return Response.json({"metrics": [m.to_dict() for m in entries]})

    def artifacts(self, request: Request, session: AuthSession | None) -> Response:
        run_id = request.params["run_id"]
        self._require_ownership("run", run_id, session)
        return Response.json(
            {
                "artifacts": ArtifactInventory(self.store).for_run(
                    run_id,
                    category=request.q("category"),
                    format=request.q("format"),
                    stage=request.q("stage"),
                )
            }
        )

    def timeline(self, request: Request, session: AuthSession | None) -> Response:
        run_id = request.params["run_id"]
        self._require_ownership("run", run_id, session)
        return Response.json({"timeline": self.index.timeline(run_id)})

    def resources(self, request: Request, session: AuthSession | None) -> Response:
        run_id = request.params["run_id"]
        self._require_ownership("run", run_id, session)
        try:
            return Response.json({"resources": ResourceQuery(self.store, self.runtime.config).run(run_id)})
        except RuntimeStoreError as exc:
            raise HttpError(404, str(exc)) from exc

    def logs(self, request: Request, session: AuthSession | None) -> Response:
        run_id = request.params["run_id"]
        self._require_ownership("run", run_id, session)
        try:
            view = LogQuery(self.store).run(
                run_id,
                offset=request.q_int("offset", 0) or 0,
                max_bytes=request.q_int("max_bytes"),
            )
        except (RuntimeStoreError, ValueError) as exc:
            raise HttpError(404 if isinstance(exc, RuntimeStoreError) else 400, str(exc)) from exc
        return Response.json({"logs": view})

    def artifact_excerpt(self, request: Request, session: AuthSession | None) -> Response:
        run_id = request.params["run_id"]
        self._require_ownership("run", run_id, session)
        offset = request.q_int("offset", 0) or 0
        max_bytes = request.q_int("max_bytes", 8192) or 8192
        try:
            excerpt = self.runtime.read_artifact_excerpt(
                run_id,
                request.params["artifact_id"],
                offset=offset,
                max_bytes=max_bytes,
            )
        except RuntimeStoreError as exc:
            raise HttpError(404, str(exc)) from exc
        except ValueError as exc:
            raise HttpError(400, str(exc)) from exc
        return Response.json(excerpt)

    def artifact_download(self, request: Request, session: AuthSession | None) -> Response:
        run_id = request.params["run_id"]
        artifact_id = request.params["artifact_id"]
        self._require_ownership("run", run_id, session)
        detail = self.index.run_detail(run_id)
        artifacts = [
            artifact
            for stage in detail.get("stages", ())
            for attempt in stage.get("attempts", ())
            for artifact in attempt.get("artifacts", ())
            if artifact.get("artifact_id") == artifact_id
        ]
        if len(artifacts) != 1:
            raise HttpError(404, "artifact not found")
        artifact = artifacts[0]
        if artifact["size_bytes"] > MAX_RESPONSE_BYTES:
            raise HttpError(413, "artifact exceeds the download response limit")
        try:
            path = self.store.artifact_path(artifact_id)
            content = path.read_bytes()
        except (OSError, RuntimeStoreError) as exc:
            raise HttpError(404, str(exc)) from exc
        from openroad_platform_runtime import sha256

        if len(content) != artifact["size_bytes"] or sha256(content) != artifact["sha256"]:
            raise HttpError(409, "artifact bytes no longer match the registered hash")
        return Response(
            body=content,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Artifact-SHA256": artifact["sha256"],
            },
        )

    def artifact_chunk(self, request: Request, session: AuthSession | None) -> Response:
        run_id = request.params["run_id"]
        self._require_ownership("run", run_id, session)
        offset = request.q_int("offset", 0) or 0
        size = request.q_int("max_bytes", 64 * 1024) or 64 * 1024
        try:
            return Response.json(self.runtime.read_artifact_excerpt(
                run_id, request.params["artifact_id"], offset=offset,
                max_bytes=size, verify_hash=False,
            ))
        except RuntimeStoreError as exc:
            raise HttpError(404, str(exc)) from exc
        except ValueError as exc:
            raise HttpError(400, str(exc)) from exc

    def graph(self, request: Request, session: AuthSession | None) -> Response:
        run_ids = request.q_all("run_id")
        if not run_ids:
            raise HttpError(400, "at least one run_id is required")
        for run_id in run_ids:
            self._require_ownership("run", run_id, session)
        return Response.json({"graph": self.index.artifact_graph(run_ids).to_dict()})

    # -- ownership --------------------------------------------------------

    def _require_ownership(self, resource_type: str, resource_id: str, session: AuthSession | None) -> None:
        owner = session.user_id if session else self.local_user_id
        if owner is None:
            return
        resource_owner = self.identity.owner_of(resource_type, resource_id)
        if resource_owner is None and not (session and session.developer):
            raise HttpError(404, f"{resource_type} not found")
        if not self.identity.owns_resource(resource_type, resource_id, owner, developer_all=True):
            # 404, not 403: telling a caller that someone else's run exists is
            # itself a disclosure.
            raise HttpError(404, f"{resource_type} not found")


def _object(request: Request) -> dict[str, Any]:
    if not isinstance(request.body, dict):
        raise HttpError(400, "request body must be a JSON object")
    return request.body
