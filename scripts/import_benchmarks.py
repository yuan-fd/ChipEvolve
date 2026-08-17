#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2-4-B: import real design benchmarks from the local ORFS flow-scripts tree.

Scans ``~/OpenROAD-flow-scripts/flow/designs/src`` for designs that ship an
RTL file, fingerprints the RTL, and registers each as a BenchmarkDefinition
in the public knowledge base (license: BSD-3-Clause per ORFS). Designs already
registered are skipped to preserve identity fingerprints.
"""
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for source in (ROOT / "packages/analysis/src", ROOT / "packages/contracts/src"):
    sys.path.insert(0, str(source))

from openroad_platform_analysis import (  # noqa: E402
    BenchmarkDefinition, PublicKnowledgeRegistry,
)

ORFS_DESIGNS = Path.home() / "OpenROAD-flow-scripts" / "flow" / "designs" / "src"
LICENSE = "BSD-3-Clause"
VERSION = "ORFS flow-scripts (local pin)"
PLATFORMS = ("nangate45", "asap7", "sky130hd", "gf180mcu")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    if not ORFS_DESIGNS.is_dir():
        print(f"ORFS designs dir missing: {ORFS_DESIGNS}")
        return 1
    registry = PublicKnowledgeRegistry(ROOT / "var" / "public" / "public-knowledge.db")
    existing = {row["benchmark_id"]
                for row in registry.list_benchmarks()}
    print("already registered:", len(existing))

    added, skipped = 0, 0
    for design_dir in sorted(ORFS_DESIGNS.iterdir()):
        if not design_dir.is_dir():
            continue
        rtl = design_dir / f"{design_dir.name}.v"
        if not rtl.is_file():
            continue
        benchmark_id = f"orfs-{design_dir.name}"
        if benchmark_id in existing:
            skipped += 1
            continue
        item = BenchmarkDefinition(
            benchmark_id=benchmark_id, source_id="orfs-docs",
            title=f"ORFS {design_dir.name}", version=VERSION,
            license_id=LICENSE, design_names=(design_dir.name,),
            entrypoint=f"flow/designs/src/{design_dir.name}/{design_dir.name}.v",
            allowed_platforms=PLATFORMS,
            rtl_sha256=_sha256(rtl),
            local_observation_eligible=False,
        )
        registry.add_benchmark(item)
        added += 1
        print(f"+ {benchmark_id} ({rtl.stat().st_size} bytes, sha256 {item.rtl_sha256[:12]}...)")

    total = len(registry.list_benchmarks())
    print(f"added={added} skipped_existing={skipped} total={total}")
    print("BENCHMARK_IMPORT_OK" if added >= 0 else "NO_IMPORT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
