#!/usr/bin/env python3
"""Repository-local launcher for the RTLScout v1 black-box adapter."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
for source_root in (
    ROOT / "packages/contracts/src",
    ROOT / "packages/execution/src",
):
    sys.path.insert(0, str(source_root))

from openroad_platform_execution.rtlscout_adapter import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
