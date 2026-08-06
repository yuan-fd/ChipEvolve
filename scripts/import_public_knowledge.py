#!/usr/bin/env python3
"""Import and verify the pinned public EDA metadata corpus."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for relative in ("packages/contracts/src", "packages/analysis/src"):
    sys.path.insert(0, str(ROOT / relative))

from openroad_platform_analysis import (  # noqa: E402
    PublicKnowledgeRegistry, load_public_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path,
                        default=ROOT / "knowledge/public-corpus.lock.json")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--query")
    parser.add_argument("--platform", default="nangate45")
    parser.add_argument("--toolchain", default="")
    parser.add_argument("--stage", default="finish")
    parser.add_argument("--design-class", default="digital")
    args = parser.parse_args()
    registry = PublicKnowledgeRegistry(args.database)
    result = registry.verify_manifest(load_public_manifest(args.manifest))
    if args.query:
        result["results"] = registry.search(
            args.query, platform=args.platform, toolchain=args.toolchain,
            stage=args.stage, design_class=args.design_class,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
