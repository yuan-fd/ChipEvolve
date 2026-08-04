#!/usr/bin/env python3
"""Adapter fixture that waits until the guardian stops it."""

from __future__ import annotations

import argparse
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.parse_args()
    time.sleep(30)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
