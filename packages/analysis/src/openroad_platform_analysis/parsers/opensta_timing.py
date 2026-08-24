"""Conservative OpenSTA text-path parser for EDAIR.

The parser extracts only explicitly labelled Startpoint/Endpoint/slack path
blocks. Unrecognized lines remain available through the raw artifact excerpt
API; they are never guessed into fields.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


START = re.compile(r"(?im)^\s*Startpoint:\s*(\S+)")
END = re.compile(r"(?im)^\s*Endpoint:\s*(\S+)")
TYPE = re.compile(r"(?im)^\s*Path\s+(?:Type|Group):\s*(\S+)")
SLACK = re.compile(
    r"(?im)^\s*(?:slack(?:\s*\([^\n)]*\))?\s+"
    r"(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|"
    r"(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s+slack(?:\s*\([^\n)]*\))?)\s*$")
DELAY = re.compile(
    r"(?im)^\s*(?:data\s+arrival\s+time\s+"
    r"(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|"
    r"(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s+data\s+arrival\s+time)\s*$")


def parse_opensta_paths(path: str | Path, *, max_paths: int = 256) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file() or not 1 <= max_paths <= 4096:
        raise ValueError("timing report or max_paths is invalid")
    text = source.read_text(encoding="utf-8", errors="replace")
    starts = list(START.finditer(text))
    rows = []
    for index, match in enumerate(starts[:max_paths]):
        block = text[match.start(): starts[index + 1].start()
                     if index + 1 < len(starts) else len(text)]
        endpoint, slack = END.search(block), SLACK.search(block)
        if endpoint is None or slack is None:
            continue
        path_type = TYPE.search(block)
        delay = DELAY.search(block)
        rows.append({
            "path_id": f"path-{index}",
            "path_type": (path_type.group(1).lower() if path_type else "setup"),
            "startpoint": match.group(1), "endpoint": endpoint.group(1),
            "slack_ns": float(slack.group(1) or slack.group(2)),
            "delay_ns": float(delay.group(1) or delay.group(2)) if delay else None,
            "points": [],
        })
    return {
        "paths": rows, "total_startpoint_blocks": len(starts),
        "truncated": len(starts) > max_paths,
        "unparsed_blocks": max(0, min(len(starts), max_paths) - len(rows)),
        "parser": "opensta-labelled-paths",
        "parser_version": "v1",
    }
