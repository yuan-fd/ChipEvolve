"""The kernel's HTTP surface.

Applications reach the kernel only through this, so that no application needs
the runtime store in-process (G5) and none can import kernel internals (G4).

Every handler is a translation: parse a request, call one kernel object, return
its answer.  There is no policy here.  If a handler ever needs to decide
something, that decision belongs in the kernel object it is calling.

Authentication is a bearer token on every route except health, registration and
login.  A route that forgot to check would be a hole nobody notices, so the
check happens in one place: ``register`` wraps each handler.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from openroad_platform_contracts import ContractError, TaskSpec
from openroad_platform_identity import AuthSession, IdentityStore
from openroad_platform_provenance import EvidenceIndex
from openroad_platform_registry import PluginRegistry, RegistryError
from openroad_platform_runtime import RuntimeStore, RuntimeStoreError, WorkflowRuntime

from .router import HttpError, Request, Response, Router

#: Routes reachable without a session.  Anything else requires one.
PUBLIC_PATHS = frozenset({
    ("GET", "/kernel/health"),
    ("POST", "/kernel/auth/register"),
    ("POST", "/kernel/auth/login"),
})


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

    # -- registration -----------------------------------------------------

    def register(self, router: Router) -> None:
        router.get("/kernel/health", self._guarded(self.health))
        router.post("/kernel/auth/register", self._guarded(self.auth_register))
        router.post("/kernel/auth/login", self._guarded(self.auth_login))
        router.get("/kernel/auth/session", self._guarded(self.auth_session))
        router.post("/kernel/auth/logout", self._guarded(self.auth_logout))

        router.get("/kernel/plugins", self._guarded(self.plugins))

        router.post("/kernel/runs", self._guarded(self.submit_run))
        router.get("/kernel/runs", self._guarded(self.list_runs))
        router.get("/kernel/runs/{run_id}", self._guarded(self.get_run))
        router.post("/kernel/runs/{run_id}/cancel", self._guarded(self.cancel_run))
        router.get("/kernel/runs/{run_id}/metrics", self._guarded(self.metrics))
        router.get("/kernel/runs/{run_id}/artifacts", self._guarded(self.artifacts))
        router.get("/kernel/runs/{run_id}/timeline", self._guarded(self.timeline))
        router.get("/kernel/runs/{run_id}/artifacts/{artifact_id}/excerpt",
                   self._guarded(self.artifact_excerpt))
        router.get("/kernel/graph", self._guarded(self.graph))

    def _guarded(self, handler: Callable[[Request, AuthSession | None], Response]
                 ) -> Callable[[Request], Response]:
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
        return Response.json({
            "service": "kernel",
            "status": "ok",
            "plugins": len(self.registry.list()),
            "admitted": sum(1 for p in self.registry.list() if p.executable),
        })

    def plugins(self, request: Request, session: AuthSession | None) -> Response:
        return Response.json({"plugins": self.registry.catalogue()})

    # -- identity ---------------------------------------------------------

    def auth_register(self, request: Request, session: AuthSession | None) -> Response:
        body = _object(request)
        try:
            auth, token = self.identity.register(
                str(body.get("username") or ""), str(body.get("password") or "")
            )
        except ValueError as exc:
            raise HttpError(400, str(exc)) from exc
        return Response.json({"token": token, "session": auth.public()})

    def auth_login(self, request: Request, session: AuthSession | None) -> Response:
        body = _object(request)
        try:
            auth, token = self.identity.login(
                str(body.get("username") or ""), str(body.get("password") or "")
            )
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

        if request.q("idempotent"):
            run = self.runtime.submit_idempotent(task)
        else:
            run = self.runtime.submit(task)
        owner = session.user_id if session else self.local_user_id
        if owner:
            self.identity.bind_resource("run", run.run_id, owner)
        return Response.json({"run": self.index.run_detail(run.run_id)}, status=201)

    def list_runs(self, request: Request, session: AuthSession | None) -> Response:
        try:
            limit = request.q_int("limit")
        except HttpError:
            raise
        try:
            summaries = self.index.runs(
                project_id=request.q("project_id"),
                design_id=request.q("design_id"),
                plugin_id=request.q("plugin_id"),
                status=request.q("status"),
                **({"limit": limit} if limit is not None else {}),
            )
        except ValueError as exc:
            raise HttpError(400, str(exc)) from exc

        if session is not None and not session.developer:
            # A member sees their own runs; a developer sees everything.  The
            # filter is applied here rather than by the caller, so a client
            # cannot widen its own view by omitting a parameter.
            owned = set(self.identity.resources_owned("run", session.user_id))
            summaries = [s for s in summaries if s.run_id in owned]
        return Response.json({"runs": [s.to_dict() for s in summaries]})

    def get_run(self, request: Request, session: AuthSession | None) -> Response:
        run_id = request.params["run_id"]
        self._require_ownership(run_id, session)
        try:
            return Response.json({"run": self.index.run_detail(run_id)})
        except RuntimeStoreError as exc:
            raise HttpError(404, str(exc)) from exc

    def cancel_run(self, request: Request, session: AuthSession | None) -> Response:
        run_id = request.params["run_id"]
        self._require_ownership(run_id, session)
        try:
            self.store.request_cancel(run_id)
        except RuntimeStoreError as exc:
            raise HttpError(404, str(exc)) from exc
        return Response.json({"run": self.index.run_detail(run_id)})

    def metrics(self, request: Request, session: AuthSession | None) -> Response:
        run_id = request.params["run_id"]
        self._require_ownership(run_id, session)
        entries = self.index.metrics(run_id, complete_only=request.q_bool("complete_only"))
        return Response.json({"metrics": [m.to_dict() for m in entries]})

    def artifacts(self, request: Request, session: AuthSession | None) -> Response:
        run_id = request.params["run_id"]
        self._require_ownership(run_id, session)
        return Response.json({"artifacts": self.index.artifacts(run_id)})

    def timeline(self, request: Request, session: AuthSession | None) -> Response:
        run_id = request.params["run_id"]
        self._require_ownership(run_id, session)
        return Response.json({"timeline": self.index.timeline(run_id)})

    def artifact_excerpt(self, request: Request,
                         session: AuthSession | None) -> Response:
        run_id = request.params["run_id"]
        self._require_ownership(run_id, session)
        offset = request.q_int("offset", 0) or 0
        max_bytes = request.q_int("max_bytes", 8192) or 8192
        try:
            excerpt = self.runtime.read_artifact_excerpt(
                run_id, request.params["artifact_id"],
                offset=offset, max_bytes=max_bytes,
            )
        except RuntimeStoreError as exc:
            raise HttpError(404, str(exc)) from exc
        except ValueError as exc:
            raise HttpError(400, str(exc)) from exc
        return Response.json(excerpt)

    def graph(self, request: Request, session: AuthSession | None) -> Response:
        run_ids = request.q_all("run_id")
        if not run_ids:
            raise HttpError(400, "at least one run_id is required")
        for run_id in run_ids:
            self._require_ownership(run_id, session)
        return Response.json({"graph": self.index.artifact_graph(run_ids).to_dict()})

    # -- ownership --------------------------------------------------------

    def _require_ownership(self, run_id: str, session: AuthSession | None) -> None:
        owner = session.user_id if session else self.local_user_id
        if owner is None:
            return
        if not self.identity.owns_resource("run", run_id, owner,
                                           developer_all=True):
            # 404, not 403: telling a caller that someone else's run exists is
            # itself a disclosure.
            raise HttpError(404, "run not found")


def _object(request: Request) -> dict[str, Any]:
    if not isinstance(request.body, dict):
        raise HttpError(400, "request body must be a JSON object")
    return request.body
