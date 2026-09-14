"""Wire the kernel and the entry point into one runnable process.

This is the composition root.  It is the only place that knows which concrete
classes make up the kernel, which is why it is separate from the entry point's
routing: adding a kernel component changes this file, and adding a route changes
``kernel_api.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openroad_platform_evaluator import PluginBackedEvaluator, resolve_evaluator
from openroad_platform_identity import IdentityStore
from openroad_platform_provenance import EvidenceIndex
from openroad_platform_registry import PluginRegistry
from openroad_platform_runtime import (
    ProcessAdapter,
    RuntimeConfig,
    RuntimeStore,
    WorkflowRuntime,
)

from .app import AppRegistration, GatewayConfig
from .kernel_api import KernelApi

#: Names the kernel records itself under, so a reader knows what it is looking at.
DEFAULT_WORKER_ID = "kernel"


@dataclass(frozen=True)
class KernelPaths:
    state_root: Path
    plugins_root: Path
    #: The platform's own trust records, one file per plugin.  Separate from the
    #: plugin directories on purpose: whether this platform admits a plugin is
    #: not the plugin's document to keep.
    admissions_root: Path

    @classmethod
    def of(cls, state_root: str | Path, plugins_root: str | Path,
           admissions_root: str | Path | None = None) -> "KernelPaths":
        state = Path(state_root).expanduser().resolve()
        return cls(
            state_root=state,
            plugins_root=Path(plugins_root).expanduser().resolve(),
            admissions_root=Path(
                admissions_root if admissions_root is not None
                else state / "admissions"
            ).expanduser().resolve(),
        )


def build_kernel(paths: KernelPaths, *, allow_anonymous: bool = False
                 ) -> KernelApi:
    """Assemble the kernel.

    A plugin directory that does not exist is an error rather than an empty
    registry: silently starting with no capabilities would look like a working
    platform that cannot do anything.
    """
    if not paths.plugins_root.is_dir():
        raise FileNotFoundError(f"plugin root not found: {paths.plugins_root}")
    paths.state_root.mkdir(parents=True, exist_ok=True)

    store = RuntimeStore(paths.state_root / "runtime.db")
    registry = PluginRegistry.from_directory(
        paths.plugins_root, admissions_root=paths.admissions_root,
    )
    identity = IdentityStore(paths.state_root / "identity.db")

    evaluator = None
    try:
        manifest = resolve_evaluator(registry)
    except Exception:  # noqa: BLE001 - a kernel without an evaluator still runs
        manifest = None
    if manifest is not None:
        evaluator = PluginBackedEvaluator(adapter=ProcessAdapter(), manifest=manifest)

    runtime = WorkflowRuntime(
        store, registry,
        config=RuntimeConfig(
            workspace_root=paths.state_root / "runtime-workspaces",
            worker_id=DEFAULT_WORKER_ID,
        ),
        protected_evaluator=evaluator,
    )

    local_user_id = None
    if allow_anonymous:
        identity.ensure_local_user()
        local_user_id = next(
            u["user_id"] for u in identity.list_users()
            if u["username"] == "local-user"
        )

    return KernelApi(
        store=store, runtime=runtime, registry=registry, identity=identity,
        index=EvidenceIndex(store), allow_anonymous=allow_anonymous,
        local_user_id=local_user_id,
    )


def build_gateway(apps: list[dict[str, Any]], kernel: KernelApi | None = None):
    config = GatewayConfig(apps=tuple(AppRegistration(**a) for a in apps))
    from .app import build_router
    return build_router(config, kernel)
