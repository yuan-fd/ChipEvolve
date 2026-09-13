"""Run the gateway: python3 -m openroad_platform_gateway --config apps.json"""

from __future__ import annotations

import argparse
import sys

from .app import GatewayConfig, serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openroad-platform-gateway")
    parser.add_argument("--config", required=True,
                        help="JSON file listing the apps to integrate")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8700)
    args = parser.parse_args(argv)
    serve(GatewayConfig.from_file(args.config), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
