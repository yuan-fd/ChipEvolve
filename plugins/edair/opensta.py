"""A conservative OpenSTA text-path parser.

Ported from the frozen v1 implementation.  "Conservative" is the whole design:

* It reads only the blocks OpenSTA explicitly labels -- ``Startpoint:``,
  ``Endpoint:``, ``Path Type:``/``Path Group:``, a slack line, a data arrival
  line.  Anything else in the report is left where it is.
* Every unlabelled or unrecognised region stays reachable through the raw
  artifact, which the platform keeps and hashes.  The parser never guesses a
  value into a field.
* The result says how much it did **not** capture: ``unparsed_blocks`` counts
  labelled blocks it could not turn into a row, and ``truncated`` says whether
  the cap cut the report short.

That last property is what makes this an index rather than a summary.  A report
with 400 paths read with ``max_paths=256`` is not a report with 256 paths, and a
reader who is not told the difference will draw the wrong conclusion from a
distribution.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

#: Parser identity, recorded with every index so a later reader knows which
#: rules produced it.
PARSER_ID = "opensta-labelled-paths"
PARSER_VERSION = "v1"

#: Default and maximum number of path blocks to extract.  A real timing report
#: can carry tens of thousands; the index is bounded and says when it was cut.
DEFAULT_MAX_PATHS = 256
MAX_PATHS_CEILING = 4096

#: A numeric literal OpenSTA may print, including exponent form.
_NUMBER = r"(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"

START = re.compile(r"(?im)^\s*Startpoint:\s*(\S+)")
END = re.compile(r"(?im)^\s*Endpoint:\s*(\S+)")
TYPE = re.compile(r"(?im)^\s*Path\s+(?:Type|Group):\s*(\S+)")

#: OpenSTA prints slack either as ``slack (VIOLATED) -0.12`` or as
#: ``-0.12 slack (VIOLATED)`` depending on version and verbosity.  Both are
#: accepted; neither is inferred from position.
SLACK = re.compile(
    r"(?im)^\s*(?:slack(?:\s*\([^\n)]*\))?\s+"
    rf"{_NUMBER}|"
    rf"{_NUMBER}\s+slack(?:\s*\([^\n)]*\))?)\s*$"
)

DELAY = re.compile(
    r"(?im)^\s*(?:data\s+arrival\s+time\s+"
    rf"{_NUMBER}|"
    rf"{_NUMBER}\s+data\s+arrival\s+time)\s*$"
)


def parse_opensta_paths(path: str | Path, *,
                        max_paths: int = DEFAULT_MAX_PATHS) -> dict[str, Any]:
    """Extract the labelled path blocks from one OpenSTA timing report.

    A labelled block that lacks an endpoint or a slack line is counted as
    unparsed and skipped.  It is never emitted with a placeholder slack: a
    missing measurement and a measured zero look identical downstream once a
    default has been invented.
    """
    source = Path(path).expanduser().resolve()
    if not source.is_file() or not 1 <= max_paths <= MAX_PATHS_CEILING:
        raise ValueError(
            f"timing report or max_paths is invalid (max_paths must be "
            f"1..{MAX_PATHS_CEILING})"
        )
    text = source.read_text(encoding="utf-8", errors="replace")
    starts = list(START.finditer(text))

    rows: list[dict[str, Any]] = []
    for index, match in enumerate(starts[:max_paths]):
        # A block runs from this Startpoint to the next one, or to the end.
        block_end = (starts[index + 1].start()
                     if index + 1 < len(starts) else len(text))
        block = text[match.start():block_end]
        endpoint = END.search(block)
        slack = SLACK.search(block)
        if endpoint is None or slack is None:
            continue
        path_type = TYPE.search(block)
        delay = DELAY.search(block)
        rows.append({
            "path_id": f"path-{index}",
            "path_type": (path_type.group(1).lower() if path_type else "setup"),
            "startpoint": match.group(1),
            "endpoint": endpoint.group(1),
            "slack_ns": float(slack.group(1) or slack.group(2)),
            "delay_ns": (float(delay.group(1) or delay.group(2))
                         if delay else None),
            # Point-level detail is not extracted.  The list is present and
            # empty so a consumer sees the field is defined and unsupported,
            # rather than absent and possibly forgotten.
            "points": [],
        })

    considered = min(len(starts), max_paths)
    return {
        "paths": rows,
        "total_startpoint_blocks": len(starts),
        "truncated": len(starts) > max_paths,
        "unparsed_blocks": max(0, considered - len(rows)),
        "parser": PARSER_ID,
        "parser_version": PARSER_VERSION,
        "loss_manifest": {
            "path_points": "not extracted; read the raw report for point detail",
            "unlabelled_lines": "left in the raw artifact, never guessed into a field",
            "truncated_at": max_paths if len(starts) > max_paths else None,
        },
    }
