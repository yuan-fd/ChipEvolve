"""In-process index of validated plugin manifests."""

from __future__ import annotations

import json
import platform
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from openroad_platform_contracts import PluginManifest


class PluginRegistry:
    def __init__(self, manifests: Iterable[PluginManifest] = ()):
        self._manifests: dict[tuple[str, str], PluginManifest] = {}
        for manifest in manifests:
            self.register(manifest)

    def register(self, manifest: PluginManifest) -> None:
        manifest.validate()
        identity = (manifest.plugin_id, manifest.plugin_version)
        if identity in self._manifests:
            raise ValueError(
                f"Plugin {manifest.plugin_id}@{manifest.plugin_version} is already registered"
            )
        self._manifests[identity] = manifest

    @classmethod
    def from_directory(cls, path: str | Path) -> "PluginRegistry":
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Plugin manifest directory not found: {root}")
        registry = cls()
        for manifest_path in sorted(root.glob("*.plugin.json")):
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"Invalid plugin manifest {manifest_path}: {exc}") from exc
            manifest = PluginManifest.from_dict(payload)
            entry = tuple(
                str((manifest_path.parent / item).resolve())
                if item.startswith("./") else item
                for item in manifest.adapter_entry
            )
            registry.register(replace(manifest, adapter_entry=entry))
        return registry

    def resolve(
        self,
        plugin_id: str,
        *,
        version: str | None = None,
        capability: str | None = None,
        arch: str | None = None,
    ) -> PluginManifest:
        matches = [
            manifest
            for (candidate_id, candidate_version), manifest in self._manifests.items()
            if candidate_id == plugin_id and (version is None or candidate_version == version)
        ]
        if not matches:
            suffix = f"@{version}" if version else ""
            raise LookupError(f"Unknown plugin: {plugin_id}{suffix}")
        if version is None and len(matches) != 1:
            versions = ", ".join(sorted(item.plugin_version for item in matches))
            raise LookupError(
                f"Plugin {plugin_id} has multiple versions; choose one of: {versions}"
            )
        manifest = matches[0]
        if capability and capability not in manifest.capabilities:
            raise LookupError(
                f"Plugin {plugin_id}@{manifest.plugin_version} lacks capability {capability}"
            )
        selected_arch = arch or platform.machine()
        if selected_arch not in manifest.supported_arch:
            raise LookupError(
                f"Plugin {plugin_id}@{manifest.plugin_version} does not support "
                f"architecture {selected_arch}"
            )
        return manifest

    def list(self) -> tuple[PluginManifest, ...]:
        return tuple(self._manifests[key] for key in sorted(self._manifests))
