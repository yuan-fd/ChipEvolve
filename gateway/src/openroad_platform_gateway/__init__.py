"""The integration entry point.

Serves the kernel's own surface and routes everything else to the application
that owns it.  Contains no domain logic and owns no database of its own.
"""

from .app import (
    APP_PREFIX,
    AppRegistration,
    build_router,
    GatewayConfig,
    KERNEL_PREFIX,
    make_handler,
    MAX_PROXY_BYTES,
    PROBE_TIMEOUT_SECONDS,
    probe,
    serve,
)
from .router import HttpError, Request, Response, Router

__all__ = (
    "APP_PREFIX", "AppRegistration", "build_router", "GatewayConfig",
    "HttpError", "KERNEL_PREFIX", "MAX_PROXY_BYTES", "make_handler",
    "PROBE_TIMEOUT_SECONDS", "probe", "Request", "Response", "Router", "serve",
)
