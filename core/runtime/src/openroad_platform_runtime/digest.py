"""The platform's one digest implementation.

G13 requires exactly one function per concern in this tree.  v1 implemented
SHA-256 in three places; when they disagree, an artifact verifies in one code
path and fails in another, and the resulting bug is nearly impossible to see.
So there is deliberately one function, and it takes either bytes or a path.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Union

#: 1 MiB.  EDA artifacts can be large; a single read would spike memory on a
#: machine that is simultaneously running a physical-design job.
CHUNK_BYTES = 1024 * 1024


def sha256(data: Union[bytes, bytearray, Path, str]) -> str:  # noqa: UP007
    """Lowercase hex SHA-256 of bytes, or of a file's contents.

    A ``str`` is treated as a filesystem path, matching how the platform names
    artifacts.  If you mean the bytes of a string, encode it first.
    """
    digest = hashlib.sha256()
    if isinstance(data, (bytes, bytearray)):
        digest.update(data)
        return digest.hexdigest()
    path = Path(data)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()
