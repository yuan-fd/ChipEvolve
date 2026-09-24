"""Process entry point for the Query-Agent HTTP service."""

from __future__ import annotations

import argparse

from .server import serve

DEFAULT_PORT = 8850


def main(argv: list[str] | None = None) -> int:
    """Parse process options and start the transport service."""
    parser = argparse.ArgumentParser(description="Query Agent")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--kernel-url", default="http://127.0.0.1:8700")
    args = parser.parse_args(argv)
    serve(host=args.host, port=args.port, kernel_url=args.kernel_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
