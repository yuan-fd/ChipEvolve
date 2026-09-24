"""The typed client an application uses to reach the kernel.

Applications may not open the kernel's database (G5) and may not import kernel
internals (G4); this is the only door.
"""

from .client import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_EXCERPT_BYTES,
    KernelClient,
    KernelError,
    KernelUnavailable,
)

__all__ = (
    "DEFAULT_TIMEOUT_SECONDS", "MAX_EXCERPT_BYTES", "KernelClient",
    "KernelError", "KernelUnavailable",
)
