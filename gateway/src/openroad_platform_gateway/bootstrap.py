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
    capacity_cpu_cores: int | None = None
    capacity_memory_bytes: int | None = None
    platform_fraction: float = 0.60

    @classmethod
    def of(cls, state_root: str | Path, plugins_root: str | Path,
           admissions_root: str | Path | None = None,
           capacity_cpu_cores: int | None = None,
           capacity_memory_bytes: int | None = None,
           platform_fraction: float = 0.60) -> "KernelPaths":
        state = Path(state_root).expanduser().resolve()
        return cls(
            state_root=state,
            plugins_root=Path(plugins_root).expanduser().resolve(),
            admissions_root=Path(
                admissions_root if admissions_root is not None
                else state / "admissions"
            ).expanduser().resolve(),
            capacity_cpu_cores=capacity_cpu_cores,
            capacity_memory_bytes=capacity_memory_bytes,
            platform_fraction=platform_fraction,
        )


@dataclass(frozen=True)
class Kernel:
    """The assembled kernel, before anything is wrapped around it.

    Every entry point builds the same three objects from the same function.
    A second assembly elsewhere would be a copy of this one, free to drift --
    and the worker's command line used to be exactly that copy, down to a
    repeated docstring claiming it was the same as the kernel's.
    """

    store: RuntimeStore
    registry: PluginRegistry
    runtime: WorkflowRuntime


def build_kernel_parts(
    paths: KernelPaths, *, worker_id: str = DEFAULT_WORKER_ID,
) -> Kernel:
    """Store, registry, evaluator and runtime, assembled once.

    A plugin directory that does not exist is an error rather than an empty
    registry: silently starting with no capabilities would look like a working
    platform that cannot do anything.
    """
    if not paths.plugins_root.is_dir():
        raise FileNotFoundError(f"plugin root not found: {paths.plugins_root}")
    paths.state_root.mkdir(parents=True, exist_ok=True)

    store = RuntimeStore(
        paths.state_root / "runtime.db",
        objects_root=paths.state_root / "runtime-objects",
    )
    registry = PluginRegistry.from_directory(
        paths.plugins_root, admissions_root=paths.admissions_root,
    )

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
            worker_id=worker_id,
            capacity_cpu_cores=paths.capacity_cpu_cores,
            capacity_memory_bytes=paths.capacity_memory_bytes,
            platform_fraction=paths.platform_fraction,
        ),
        protected_evaluator=evaluator,
    )
    return Kernel(store=store, registry=registry, runtime=runtime)


def build_kernel(paths: KernelPaths, *, allow_anonymous: bool = False
                 ) -> KernelApi:
    """The kernel with its HTTP surface: the entry point's assembly."""
    parts = build_kernel_parts(paths)
    store, registry, runtime = parts.store, parts.registry, parts.runtime
    identity = IdentityStore(paths.state_root / "identity.db")

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
