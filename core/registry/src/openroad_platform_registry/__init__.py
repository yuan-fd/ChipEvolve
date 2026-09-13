"""Plugin discovery and admission.

The kernel learns what capabilities exist by reading the plugins directory.
There is no list of plugin names anywhere in this package, and G1 enforces that.
"""

from .registry import (
    ADMITTED,
    Admission,
    EXECUTABLE_LICENSES,
    INTAKE_FILENAME,
    MANIFEST_SUFFIX,
    PluginRegistry,
    RegisteredPlugin,
    RegistryError,
    SOURCE_AUDIT_ONLY,
    UNKNOWN,
)

__all__ = (
    "ADMITTED", "Admission", "EXECUTABLE_LICENSES", "INTAKE_FILENAME",
    "MANIFEST_SUFFIX", "PluginRegistry", "RegisteredPlugin", "RegistryError",
    "SOURCE_AUDIT_ONLY", "UNKNOWN",
)
