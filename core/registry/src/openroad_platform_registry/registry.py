"""Plugin discovery and admission.

Two things the previous platform got wrong, both fixed here.

**Discovery was decorative.** A directory-scanning constructor existed but only
tests called it; production wired ten manifests by hand inside the API class.
Adding a plugin meant editing the platform. Here discovery is the only path:
``PluginRegistry.from_directory`` is how a capability becomes known.

**Admission was unenforced.** Manifests recorded a license conclusion that
nothing checked, so a plugin with no license could still be resolved and run.
Here a plugin without intake evidence is discoverable but not executable, and
resolution says so by name.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from openroad_platform_contracts import (
    ContractError,
    PluginManifest,
    validate_identifier,
)

MANIFEST_SUFFIX = ".plugin.json"
INTAKE_FILENAME = "intake.json"

#: Admission outcomes.  Only ``admitted`` may be executed.
ADMITTED = "admitted"
SOURCE_AUDIT_ONLY = "source-audit-only"
UNKNOWN = "unknown"

#: License conclusions that permit redistribution and execution.
EXECUTABLE_LICENSES = frozenset({"green", "yellow"})


class RegistryError(RuntimeError):
    """The registry refused an operation.  Never swallowed into a default."""


@dataclass(frozen=True)
class Admission:
    """The governance record for one plugin.

    ``reason`` is mandatory on a non-admitted plugin.  "Not admitted" with no
    explanation is how a blocked capability quietly becomes a missing one.
    """

    plugin_id: str
    status: str = UNKNOWN
    license: str | None = None
    source_url: str | None = None
    source_commit: str | None = None
    reason: str | None = None

    def validate(self) -> None:
        validate_identifier("plugin_id", self.plugin_id)
        if self.status not in {ADMITTED, SOURCE_AUDIT_ONLY, UNKNOWN}:
            raise RegistryError(f"unknown admission status {self.status!r}")
        if self.status == ADMITTED:
            if (self.license or "").lower() not in EXECUTABLE_LICENSES:
                raise RegistryError(
                    f"{self.plugin_id!r} is marked admitted but its license "
                    f"conclusion is {self.license!r}; admitted requires one of "
                    f"{sorted(EXECUTABLE_LICENSES)}"
                )
            if not self.source_commit:
                raise RegistryError(
                    f"{self.plugin_id!r} is admitted without a pinned commit; "
                    f"an unpinned branch tip is not admissible"
                )
        elif not self.reason:
            raise RegistryError(
                f"{self.plugin_id!r} is {self.status!r} with no reason recorded"
            )

    @classmethod
    def from_dict(cls, plugin_id: str, payload: Mapping[str, Any]) -> "Admission":
        known = {"status", "license", "source_url", "source_commit", "reason"}
        unknown = sorted(set(payload) - known)
        if unknown:
            raise RegistryError(
                f"unknown intake fields for {plugin_id!r}: {', '.join(unknown)}"
            )
        admission = cls(
            plugin_id=plugin_id,
            status=str(payload.get("status", UNKNOWN)),
            license=payload.get("license"),
            source_url=payload.get("source_url"),
            source_commit=payload.get("source_commit"),
            reason=payload.get("reason"),
        )
        admission.validate()
        return admission


@dataclass(frozen=True)
class RegisteredPlugin:
    manifest: PluginManifest
    admission: Admission
    manifest_path: str = ""
    adapter_entry: tuple[str, ...] = field(default=())

    @property
    def executable(self) -> bool:
        return self.admission.status == ADMITTED


class PluginRegistry:
    """An index of validated manifests and their admission state."""

    def __init__(self, plugins: Iterable[RegisteredPlugin] = ()):
        self._plugins: dict[tuple[str, str], RegisteredPlugin] = {}
        for plugin in plugins:
            self.register(plugin)

    # -- construction -----------------------------------------------------

    @classmethod
    def from_directory(cls, root: str | Path) -> "PluginRegistry":
        """Discover every plugin under ``root``.

        Expected shape::

            <root>/<name>/<name>.plugin.json
            <root>/<name>/intake.json        (admission evidence)
            <root>/<name>/<adapter>

        A plugin directory with no manifest is skipped: it may be a half-written
        checkout, and inventing a manifest for it would be worse than ignoring
        it.  A plugin with a manifest but no intake is registered as UNKNOWN, so
        it appears in the catalogue and refuses to execute.
        """
        base = Path(root).expanduser().resolve()
        if not base.is_dir():
            raise RegistryError(f"plugin root not found: {base}")
        registry = cls()
        for directory in sorted(p for p in base.iterdir() if p.is_dir()):
            for manifest_path in sorted(directory.glob(f"*{MANIFEST_SUFFIX}")):
                registry.register(
                    _load_plugin(manifest_path, manifest_path.parent)
                )
        return registry

    def register(self, plugin: RegisteredPlugin) -> None:
        plugin.manifest.validate()
        plugin.admission.validate()
        if plugin.admission.plugin_id != plugin.manifest.plugin_id:
            raise RegistryError(
                f"intake declares {plugin.admission.plugin_id!r} but the "
                f"manifest declares {plugin.manifest.plugin_id!r}"
            )
        identity = (plugin.manifest.plugin_id, plugin.manifest.plugin_version)
        if identity in self._plugins:
            raise RegistryError(
                f"plugin {identity[0]}@{identity[1]} is already registered"
            )
        self._plugins[identity] = plugin

    # -- lookup -----------------------------------------------------------

    def resolve(
        self, plugin_id: str, *, version: str | None = None,
        capability: str | None = None, arch: str | None = None,
        require_admitted: bool = True,
    ) -> PluginManifest:
        matches = [
            plugin for (candidate_id, _), plugin in self._plugins.items()
            if candidate_id == plugin_id
            and (version is None or plugin.manifest.plugin_version == version)
        ]
        if not matches:
            suffix = f"@{version}" if version else ""
            raise RegistryError(f"unknown plugin: {plugin_id}{suffix}")
        if len(matches) > 1:
            versions = ", ".join(sorted(p.manifest.plugin_version for p in matches))
            raise RegistryError(
                f"plugin {plugin_id!r} has several versions; choose one of: "
                f"{versions}"
            )
        plugin = matches[0]

        if require_admitted and not plugin.executable:
            raise RegistryError(
                f"plugin {plugin_id!r} is not admitted "
                f"({plugin.admission.status}: {plugin.admission.reason})"
            )
        if capability and capability not in plugin.manifest.capabilities:
            raise RegistryError(
                f"plugin {plugin_id}@{plugin.manifest.plugin_version} lacks "
                f"capability {capability!r}"
            )
        if arch and arch not in plugin.manifest.supported_arch:
            raise RegistryError(
                f"plugin {plugin_id}@{plugin.manifest.plugin_version} does not "
                f"support architecture {arch!r}"
            )
        return plugin.manifest

    def get(self, plugin_id: str) -> RegisteredPlugin:
        matches = [
            plugin for (candidate_id, _), plugin in self._plugins.items()
            if candidate_id == plugin_id
        ]
        if len(matches) != 1:
            raise RegistryError(
                f"expected exactly one registered version of {plugin_id!r}, "
                f"found {len(matches)}"
            )
        return matches[0]

    def list(self) -> tuple[RegisteredPlugin, ...]:
        return tuple(
            self._plugins[key] for key in sorted(self._plugins)
        )

    def catalogue(self) -> list[dict[str, Any]]:
        """A read model for an app that lists capabilities and their state."""
        return [
            {
                "plugin_id": p.manifest.plugin_id,
                "plugin_version": p.manifest.plugin_version,
                "capabilities": list(p.manifest.capabilities),
                "admission": p.admission.status,
                "executable": p.executable,
                "reason": p.admission.reason,
                "adapter_entry": list(p.adapter_entry or p.manifest.adapter_entry),
            }
            for p in self.list()
        ]


def _load_plugin(manifest_path: Path, directory: Path) -> RegisteredPlugin:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"invalid manifest {manifest_path}: {exc}") from exc
    try:
        manifest = PluginManifest.from_dict(payload)
    except ContractError as exc:
        raise RegistryError(f"invalid manifest {manifest_path}: {exc}") from exc

    # ``./``-prefixed entries resolve relative to the manifest's own directory,
    # which is what lets a plugin ship its adapter next to its manifest.  An
    # entry that climbs out with ``..`` would let a "plugin" point back into the
    # platform, which is precisely the v1 defect being removed.
    entry: list[str] = []
    for item in manifest.adapter_entry:
        if item.startswith("./"):
            candidate = (directory / item[2:]).resolve()
            try:
                candidate.relative_to(directory.resolve())
            except ValueError as exc:
                raise RegistryError(
                    f"{manifest.plugin_id!r} adapter entry escapes its own "
                    f"directory: {item!r}"
                ) from exc
            entry.append(str(candidate))
        elif item.startswith("../") or "/../" in item:
            raise RegistryError(
                f"{manifest.plugin_id!r} adapter entry climbs out of its "
                f"directory: {item!r}"
            )
        else:
            entry.append(item)

    # The manifest the runtime receives carries resolved adapter paths.
    # Leaving "./adapter.py" in place would make the entry relative to the
    # attempt workspace, where no such file exists -- a mistake that only
    # shows up when a real plugin is executed, which is exactly what the
    # end-to-end test does.
    resolved = dataclasses.replace(manifest, adapter_entry=tuple(entry))
    admission = _load_admission(manifest.plugin_id, directory)
    return RegisteredPlugin(
        manifest=resolved, admission=admission,
        manifest_path=str(manifest_path), adapter_entry=tuple(entry),
    )


def _load_admission(plugin_id: str, directory: Path) -> Admission:
    path = directory / INTAKE_FILENAME
    if not path.is_file():
        return Admission(
            plugin_id=plugin_id, status=UNKNOWN,
            reason=f"no {INTAKE_FILENAME} in the plugin directory; "
                   f"admission evidence is required before execution",
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return Admission(
            plugin_id=plugin_id, status=UNKNOWN,
            reason=f"{INTAKE_FILENAME} is unreadable: {exc}",
        )
    return Admission.from_dict(plugin_id, payload)
