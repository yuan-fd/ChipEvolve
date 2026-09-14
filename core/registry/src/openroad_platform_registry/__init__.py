"""Plugin discovery and admission.

The kernel learns what capabilities exist by reading the plugins directory.
There is no list of plugin names anywhere in this package, and G1 enforces that.

Two answers live in two places.  A plugin declares its own origin
(``provenance.json``, in its own directory); this platform records whether it
admits the plugin (``<admissions_root>/<plugin_id>.json``).  Only the second
grants execution.
"""

from .registry import (
    ADMISSION_SUFFIX,
    ADMITTED,
    Admission,
    EXECUTABLE_LICENSES,
    MANIFEST_SUFFIX,
    PROVENANCE_FILENAME,
    PluginRegistry,
    Provenance,
    RegisteredPlugin,
    RegistryError,
    SOURCE_AUDIT_ONLY,
    UNKNOWN,
)

__all__ = (
    "ADMISSION_SUFFIX", "ADMITTED", "Admission", "EXECUTABLE_LICENSES",
    "MANIFEST_SUFFIX", "PROVENANCE_FILENAME", "PluginRegistry", "Provenance",
    "RegisteredPlugin", "RegistryError", "SOURCE_AUDIT_ONLY", "UNKNOWN",
)
