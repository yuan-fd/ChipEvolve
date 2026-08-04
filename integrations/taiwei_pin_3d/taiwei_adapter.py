#!/usr/bin/env python3
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
for source in (ROOT / "packages/contracts/src", ROOT / "packages/execution/src"):
    sys.path.insert(0, str(source))
from openroad_platform_execution.taiwei_adapter import main
if __name__ == "__main__":
    raise SystemExit(main())
