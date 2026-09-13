"""Run the platform: python3 -m openroad_platform_gateway --state-root ... 

One process serves the kernel surface and routes to the applications listed in
the configuration file.
"""

from __future__ import annotations

import argparse
import sys

from .app import GatewayConfig, build_router, make_handler
from .bootstrap import KernelPaths, build_kernel


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openroad-platform-gateway")
    parser.add_argument("--config", help="JSON file listing the apps to integrate")
    parser.add_argument("--state-root", required=True,
                        help="directory holding the kernel's durable state")
    parser.add_argument("--plugins-root", default="plugins",
                        help="directory scanned for plugin manifests")
    parser.add_argument("--no-auth", action="store_true",
                        help="skip authentication; binds everything to local-user")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8700)
    args = parser.parse_args(argv)

    config = (GatewayConfig.from_file(args.config) if args.config
              else GatewayConfig())
    kernel = build_kernel(
        KernelPaths.of(args.state_root, args.plugins_root),
        allow_anonymous=args.no_auth,
    )
    router = build_router(config, kernel)

    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer((args.host, args.port), make_handler(router))
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
