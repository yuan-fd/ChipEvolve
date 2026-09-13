"""The integration entry point.

Authenticates, routes, and aggregates navigation.  Contains no domain logic,
owns no database, and knows nothing about any capability.  G1 enforces that.
"""

from .app import (
    AppRegistration,
    GatewayConfig,
    make_handler,
    MAX_PROXY_BYTES,
    PROBE_TIMEOUT_SECONDS,
    probe,
    serve,
)

__all__ = (
    "AppRegistration", "GatewayConfig", "make_handler", "MAX_PROXY_BYTES",
    "PROBE_TIMEOUT_SECONDS", "probe", "serve",
)
