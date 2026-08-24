#!/usr/bin/env python3
"""Persist the three immutable v2 paper protocols before running experiments."""
from __future__ import annotations
import argparse
from pathlib import Path
from openroad_platform_analysis import PaperProtocolStore
from openroad_platform_analysis.v2_research import v2_research_protocols

parser = argparse.ArgumentParser(); parser.add_argument("--database", type=Path, required=True)
args = parser.parse_args(); store = PaperProtocolStore(str(args.database))
for name, protocol in v2_research_protocols().items():
    print(f"{name}\t{store.add(protocol)}")
