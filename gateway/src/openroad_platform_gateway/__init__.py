"""The integration entry point.

Serves the kernel's own surface and routes everything else to the application
that owns it.  Contains no domain logic and owns no database of its own.
"""

from .app import (
    APP_PREFIX,
    KERNEL_PREFIX,
    MAX_PROXY_BYTES,
    PROBE_TIMEOUT_SECONDS,
    AppRegistration,
    GatewayConfig,
    build_router,
    make_handler,
    probe,
    serve,
)
from .router import HttpError, Request, Response, Router

__all__ = (
    "APP_PREFIX",
    "KERNEL_PREFIX",
    "MAX_PROXY_BYTES",
    "PROBE_TIMEOUT_SECONDS",
    "AppRegistration",
    "GatewayConfig",
    "HttpError",
    "Request",
    "Response",
    "Router",
    "build_router",
    "make_handler",
    "probe",
    "serve",
)
